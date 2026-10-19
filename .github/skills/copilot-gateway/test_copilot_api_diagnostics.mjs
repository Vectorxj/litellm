import assert from "node:assert/strict";
import { test } from "node:test";
import { isContextWindowError, createDiagnosticFetch, requestMetadata } from "./copilot-api-diagnostics.mjs";

test("only the known context rejection is classified as a context error", () => {
  const body = { error: {
    message: "Your input exceeds the context window of this model. Please adjust your input and try again.",
    code: "invalid_request_body",
  } };
  assert.equal(isContextWindowError(400, body), true);
  assert.equal(isContextWindowError(400, { error: { code: "invalid_request_body", message: "Different error" } }), false);
  assert.equal(isContextWindowError(429, body), false);
});

test("diagnostic streaming preserves bytes and records terminal usage without content or secrets", async () => {
  const events = [];
  const content = 'data: {"type":"response.output_text.delta","delta":"private output"}\n\n'
    + 'data: {"type":"response.completed","response":{"model":"gpt-6.1-sol","max_output_tokens":128000,'
    + '"usage":{"input_tokens":123,"output_tokens":5}}}\n\n';
  const transport = createDiagnosticFetch(async () => new Response(content, {
    headers: { "content-type": "text/event-stream", "x-github-request-id": "test-request", "set-cookie": "secret" },
  }), (event) => { events.push(event); });
  const result = await transport("https://api.githubcopilot.com/responses", {
    headers: { Authorization: "Bearer secret" },
    body: JSON.stringify({ model: "gpt-6.1-sol", stream: true, input: [
      { type: "reasoning", encrypted_content: "private ciphertext" },
      { role: "user", content: "private prompt" },
      { type: "compaction", encrypted_content: "private checkpoint" },
    ] }),
  });
  assert.equal(await result.text(), content);
  assert.equal(events.at(-1).phase, "eof");
  assert.equal(events.at(-1).terminal, "response.completed");
  assert.deepEqual(events.at(-1).usage, { input_tokens: 123, output_tokens: 5 });
  assert.equal(events.at(-1).reported_model, "gpt-6.1-sol");
  assert.equal(events.at(-1).reported_max_output_tokens, 128000);
  assert.equal(events[0].compaction_sha256.length, 1);
  assert.equal(events[1].headers["x-github-request-id"], "test-request");
  for (const secret of ["secret", "private output", "private prompt", "private ciphertext", "private checkpoint"]) {
    assert.equal(JSON.stringify(events).includes(secret), false);
  }
});

test("transport errors stay errors and missing terminal EOF stays incomplete", async () => {
  const events = [];
  const broken = new ReadableStream({ pull(controller) { controller.error(new TypeError("connection lost")); } });
  const transport = createDiagnosticFetch(async () => new Response(broken, {
    headers: { "content-type": "text/event-stream" },
  }), (event) => { events.push(event); });
  const response = await transport("https://api.githubcopilot.com/responses", { body: '{"input":[]}' });
  await assert.rejects(response.text(), /connection lost/);
  assert.equal(events.at(-1).phase, "body_error");
  assert.equal(events.at(-1).terminal, null);
  const clean = createDiagnosticFetch(async () => new Response('data: {"type":"response.created"}\n\n', {
    headers: { "content-type": "text/event-stream" },
  }), (event) => { events.push(event); });
  await (await clean("https://api.githubcopilot.com/responses", { body: '{"input":[]}' })).text();
  assert.equal(events.at(-1).phase, "eof");
  assert.equal(events.at(-1).terminal, null);
  assert.equal(requestMetadata({ input: [{ type: "message" }, { type: "message" }] }).duplicate_item_count, 1);
});

test("HTTP rejection retains status and body while diagnostic records exclude error text", async () => {
  const events = [];
  const body = '{"error":{"code":"invalid_request_body","message":"private request detail"}}';
  const transport = createDiagnosticFetch(async () => new Response(body, { status: 400 }),
    (event) => { events.push(event); });
  const response = await transport("https://api.githubcopilot.com/responses", { body: '{"input":[]}' });
  assert.equal(response.status, 400);
  assert.equal(await response.text(), body);
  assert.equal(events.at(-1).error_code, "invalid_request_body");
  assert.equal(JSON.stringify(events).includes("private request detail"), false);
});

test("client cancellation is not misclassified as an upstream body error", async () => {
  const events = [];
  let cancelled = false;
  const source = new ReadableStream({ cancel() { cancelled = true; } });
  const transport = createDiagnosticFetch(async () => new Response(source, {
    headers: { "content-type": "text/event-stream" },
  }), (event) => { events.push(event); });
  const response = await transport("https://api.githubcopilot.com/responses", { body: '{"input":[]}' });
  const reader = response.body.getReader();
  const pending = reader.read();
  await reader.cancel();
  assert.equal((await pending).done, true);
  assert.equal(cancelled, true);
  assert.equal(events.filter((event) => event.phase === "client_cancel").length, 1);
  assert.equal(events.some((event) => event.phase === "body_error"), false);
});

test("log failures are surfaced without inventing an upstream transport error", async () => {
  const events = [];
  const transport = createDiagnosticFetch(async () => new Response('data: {"type":"response.created"}\n\n', {
    headers: { "content-type": "text/event-stream" },
  }), (event) => {
    events.push(event);
    if (event.phase === "eof") throw new Error("diagnostic log unavailable");
  });
  const response = await transport("https://api.githubcopilot.com/responses", { body: '{"input":[]}' });
  await assert.rejects(response.text(), /diagnostic log unavailable/);
  assert.equal(events.some((event) => event.phase === "body_error"), false);
});
