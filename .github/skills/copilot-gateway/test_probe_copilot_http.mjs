import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import http from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { comparisonHeaders, createSseSummary, runProbe } from "./probe-copilot-http.mjs";
import { analyzeSequence, sequencePrompt, verifySequence } from "./verify-sequence-stream.mjs";

test("independent probe observes fragmented SSE without retaining private content", () => {
  const summary = createSseSummary();
  const data = Buffer.from('data: {"type":"response.output_text.delta","delta":"private text"}\n\n'
    + 'data: {"type":"response.completed","response":{"model":"gpt-5.6-sol","max_output_tokens":128000,'
    + '"usage":{"input_tokens":100,"output_tokens":2}}}\n\n');
  for (let position = 0; position < data.length; position += 7) summary.push(data.subarray(position, position + 7));
  assert.deepEqual(summary.snapshot().event_counts, { "response.output_text.delta": 1, "response.completed": 1 });
  assert.equal(summary.snapshot().terminal, "response.completed");
  assert.equal(summary.snapshot().usage.input_tokens, 100);
  assert.equal(summary.snapshot().reported_model, "gpt-5.6-sol");
  assert.equal(summary.snapshot().reported_max_output_tokens, 128000);
  assert.equal(JSON.stringify(summary.snapshot()).includes("private text"), false);
  assert.equal(comparisonHeaders.includes("authorization"), false);
  assert.equal(comparisonHeaders.includes("cookie"), false);
});

test("EOF without a terminal event remains incomplete", () => {
  const summary = createSseSummary();
  summary.push(Buffer.from('data: {"type":"response.output_item.done"}\n\ndata: [DONE]\n\n'));
  assert.equal(summary.snapshot().terminal, null);
  summary.push(Buffer.from('data: {"type":"error","message":"private error detail","code":"service_unavailable"}\n\n'));
  assert.equal(summary.snapshot().terminal, "error");
  assert.equal(summary.snapshot().error_code, "service_unavailable");
  assert.equal(JSON.stringify(summary.snapshot()).includes("private error detail"), false);
});

test("text is inspected even when no terminal event arrives", () => {
  const fragments = [];
  const summary = createSseSummary((delta) => fragments.push(delta));
  summary.push(Buffer.from('data: {"type":"response.output_text.delta","delta":"{\\"rows\\":[1,2,3]}"}\n\n'));
  assert.equal(summary.snapshot().terminal, null);
  assert.equal(analyzeSequence(fragments.join(""), 3).exact_sequence, true);
  assert.equal(JSON.stringify(summary.snapshot()).includes("rows"), false);
});

test("a cut number is distinguished from a fully received integer", () => {
  const result = analyzeSequence('{"rows":[1,2,3,4', 40);
  assert.equal(result.json_valid, false);
  assert.equal(result.last_delimited_number, 3);
  assert.equal(result.contiguous_prefix, 3);
  assert.equal(result.trailing_fragment, "4");
  assert.equal(result.exact_sequence, false);
  const closedArray = analyzeSequence('{"rows":[1,2,3]', 3);
  assert.equal(closedArray.array_closed, true);
  assert.equal(closedArray.last_delimited_number, 3);
  assert.equal(closedArray.json_valid, false);
  assert.equal(closedArray.exact_sequence, false);
  assert.equal(analyzeSequence('{"rows":[1,2,3]}', 3).exact_sequence, true);
  assert.equal(analyzeSequence('{"rows":[1,2,3]}', 4).exact_sequence, false);
  assert.deepEqual(analyzeSequence('{"rows":[1,3,2]}', 3).first_mismatch,
    { position: 2, expected: 2, observed: 3 });
});

for (const throws of [false, true]) test(`received text is retained and validated after failure, throws=${throws}`, async () => {
  const directory = await mkdtemp(join(tmpdir(), "copilot-sequence-probe-"));
  const text = throws ? '{"rows":[1,2,3]}' : '{"rows":[1,2,3';
  try {
    await writeFile(join(directory, "upstream-request.private.body"), JSON.stringify({
      input: sequencePrompt(3),
      text: { format: { schema: { properties: { rows: { minItems: 3, maxItems: 3 } } } } },
    }), { mode: 0o600 });
    const promise = verifySequence(directory, async (_directory, onText) => {
      onText(text);
      if (throws) throw new Error("transport interrupted");
      return { terminal: null, reported_model: "fixture" };
    });
    if (throws) await assert.rejects(promise, /transport interrupted/);
    else await promise;
    assert.equal(await readFile(join(directory, "output-text.txt"), "utf8"), text);
    const analysis = JSON.parse(await readFile(join(directory, "content-analysis.json"), "utf8"));
    assert.equal(analysis.protocol_terminal, null);
    assert.equal(analysis.exact_sequence, throws);
    assert.equal(analysis.last_delimited_number, throws ? 3 : 2);
  } finally {
    await rm(directory, { recursive: true });
  }
});

test("synthetic content capture refuses unrelated context", async () => {
  const directory = await mkdtemp(join(tmpdir(), "copilot-sequence-probe-"));
  try {
    await writeFile(join(directory, "upstream-request.private.body"), JSON.stringify({
      input: sequencePrompt(3), instructions: "Unrelated private context",
    }), { mode: 0o600 });
    await assert.rejects(verifySequence(directory), /cannot include other context/);
  } finally {
    await rm(directory, { recursive: true });
  }
});

for (const completeHttp of [false, true]) test(`HTTP complete=${completeHttp} does not imply response.completed`, async () => {
  const directory = await mkdtemp(join(tmpdir(), "copilot-http-probe-"));
  const server = http.createServer((request, response) => {
    request.resume();
    request.on("end", () => {
      response.writeHead(200, { "content-type": "text/event-stream" });
      if (completeHttp) {
        response.end('data: {"type":"response.output_text.delta","delta":"partial"}\n\n');
      } else {
        response.write('data: {"type":"response.output_item.done"}\n\n');
        setTimeout(() => response.socket.end(), 25);
      }
    });
  });
  try {
    await writeFile(join(directory, "proxy-key"), "fixture-key", { mode: 0o600 });
    await writeFile(join(directory, "client-request.private.json"), "{}", { mode: 0o600 });
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    const observed = await runProbe("gateway", directory, server.address().port);
    const result = JSON.parse(await readFile(join(directory, "gateway-result.json"), "utf8"));
    assert.deepEqual(observed, result);
    assert.equal(result.peer_end_observed, !completeHttp);
    assert.equal(result.probe_aborted, false);
    assert.equal(result.http_complete, completeHttp);
    assert.equal(result.terminal, null);
    assert.equal(result.transport_error_code, completeHttp ? null : "ECONNRESET");
    assert.equal(result.error_code, null);
  } finally {
    await new Promise((resolve) => server.close(resolve));
    await rm(directory, { recursive: true });
  }
});
