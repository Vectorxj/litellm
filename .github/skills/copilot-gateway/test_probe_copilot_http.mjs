import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import http from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { comparisonHeaders, createSseSummary, runProbe } from "./probe-copilot-http.mjs";

test("independent probe observes fragmented SSE without retaining private content", () => {
  const summary = createSseSummary();
  const data = Buffer.from('data: {"type":"response.output_text.delta","delta":"private text"}\n\n'
    + 'data: {"type":"response.completed","response":{"usage":{"input_tokens":100,"output_tokens":2}}}\n\n');
  for (let position = 0; position < data.length; position += 7) summary.push(data.subarray(position, position + 7));
  assert.deepEqual(summary.snapshot().event_counts, { "response.output_text.delta": 1, "response.completed": 1 });
  assert.equal(summary.snapshot().terminal, "response.completed");
  assert.equal(summary.snapshot().usage.input_tokens, 100);
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

test("a real peer EOF is distinguished from local cancellation and an application error", async () => {
  const directory = await mkdtemp(join(tmpdir(), "copilot-http-probe-"));
  const server = http.createServer((request, response) => {
    request.resume();
    request.on("end", () => {
      response.writeHead(200, { "content-type": "text/event-stream" });
      response.write('data: {"type":"response.output_item.done"}\n\n');
      setTimeout(() => response.socket.end(), 25);
    });
  });
  try {
    await writeFile(join(directory, "proxy-key"), "fixture-key", { mode: 0o600 });
    await writeFile(join(directory, "client-request.private.json"), "{}", { mode: 0o600 });
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    const observed = await runProbe("gateway", directory, server.address().port);
    const result = JSON.parse(await readFile(join(directory, "gateway-result.json"), "utf8"));
    assert.deepEqual(observed, result);
    assert.equal(result.peer_end_observed, true);
    assert.equal(result.probe_aborted, false);
    assert.equal(result.http_complete, false);
    assert.equal(result.terminal, null);
    assert.equal(result.transport_error_code, "ECONNRESET");
    assert.equal(result.error_code, null);
  } finally {
    await new Promise((resolve) => server.close(resolve));
    await rm(directory, { recursive: true });
  }
});
