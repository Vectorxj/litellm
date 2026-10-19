import { createHash, randomUUID } from "node:crypto";
import { appendFile } from "node:fs/promises";
import { performance } from "node:perf_hooks";

const contextMessage = "Your input exceeds the context window of this model. Please adjust your input and try again.";
const hash = (value) => createHash("sha256").update(JSON.stringify(value)).digest("hex");
const code = (value) => typeof value === "string" && /^[a-zA-Z0-9_.-]{1,80}$/.test(value) ? value : null;

export function isContextWindowError(status, body) {
  return status === 400 && body?.error?.code === "invalid_request_body"
    && body.error.message === contextMessage;
}

export function requestMetadata(body) {
  const items = Array.isArray(body.input) ? body.input : [];
  const fingerprints = items.map(hash);
  const encrypted = items.filter((item) => typeof item.encrypted_content === "string");
  return {
    model: body.model,
    stream: body.stream,
    reasoning_effort: body.reasoning?.effort,
    max_output_tokens: body.max_output_tokens ?? null,
    input_sha256: hash(body.input ?? null),
    input_items: items.length,
    input_types: Object.fromEntries([...new Set(items.map((item) => item.type ?? item.role))].map(
      (kind) => [kind, items.filter((item) => (item.type ?? item.role) === kind).length],
    )),
    duplicate_item_count: fingerprints.length - new Set(fingerprints).size,
    encrypted_item_count: encrypted.length,
    encrypted_characters: encrypted.reduce((count, item) => count + item.encrypted_content.length, 0),
    duplicate_encrypted_item_count: encrypted.length - new Set(encrypted.map((item) => hash(item.encrypted_content))).size,
    compaction_sha256: items.filter((item) => item.type === "compaction").map(hash),
    compaction_trigger: items.some((item) => item.type === "compaction_trigger"),
    tool_count: Array.isArray(body.tools) ? body.tools.length : 0,
  };
}

export function createDiagnosticFetch(fetchImpl, record) {
  return async (url, init) => {
    if (new URL(url).pathname !== "/responses") return fetchImpl(url, init);
    const started = performance.now();
    const id = randomUUID();
    const body = JSON.parse(init.body);
    const base = {
      id,
      endpoint: new URL(url).pathname,
      request_body_bytes: Buffer.byteLength(init.body),
      ...requestMetadata(body),
    };
    const emit = (event) => record({
      ...base,
      at: new Date().toISOString(),
      elapsed_ms: Math.round(performance.now() - started),
      ...event,
    });
    await emit({ phase: "request" });
    let response;
    try {
      response = await fetchImpl(url, init);
    } catch (error) {
      await emit({ phase: "headers_error", error_type: error.name, cause_code: error.cause?.code });
      throw error;
    }
    const headers = Object.fromEntries(
      ["x-github-request-id", "x-copilot-service-request-id", "x-request-id", "content-type", "transfer-encoding"]
        .filter((key) => response.headers.has(key))
        .map((key) => [key, response.headers.get(key)]),
    );
    await emit({ phase: "headers", status: response.status, headers });
    if (!response.ok) {
      const text = await response.clone().text();
      let errorBody;
      try {
        errorBody = JSON.parse(text);
      } catch (error) {
        if (!(error instanceof SyntaxError)) throw error;
        await emit({ phase: "http_error", status: response.status, body_format: "non_json" });
        return response;
      }
      await emit({
        phase: "http_error",
        status: response.status,
        error_code: code(errorBody.error?.code),
        context_window_exceeded: isContextWindowError(response.status, errorBody),
      });
      return response;
    }
    if (!response.body || !response.headers.get("content-type")?.includes("text/event-stream")) return response;
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let bytes = 0;
    let chunks = 0;
    let firstByteMs = null;
    let lastByteMs = null;
    let lastEvent = null;
    let terminal = null;
    let errorCode = null;
    let usage = null;
    let reportedModel = null;
    let reportedOutputLimit = null;
    let oversizedFrames = 0;
    let skipLine = false;
    let finished = false;
    const summary = () => ({
      bytes, chunks, first_byte_ms: firstByteMs, last_byte_ms: lastByteMs,
      last_event: lastEvent, terminal, error_code: errorCode, usage, oversized_frames: oversizedFrames,
      request_signal_aborted: init.signal?.aborted ?? false,
      reported_model: reportedModel, reported_max_output_tokens: reportedOutputLimit,
    });
    return new Response(new ReadableStream({
      async pull(controller) {
        let result;
        try {
          result = await reader.read();
        } catch (error) {
          if (finished) return;
          finished = true;
          try {
            await emit({ phase: "body_error", ...summary(), error_type: error.name, cause_code: error.cause?.code });
          } finally {
            reader.releaseLock();
            controller.error(error);
          }
          return;
        }
        if (finished) return;
        if (result.done) {
          finished = true;
          try {
            await emit({ phase: "eof", ...summary() });
          } finally {
            reader.releaseLock();
          }
          controller.close();
          return;
        }
        const elapsed = Math.round(performance.now() - started);
        firstByteMs ??= elapsed;
        lastByteMs = elapsed;
        chunks += 1;
        bytes += result.value.length;
        buffer += decoder.decode(result.value, { stream: true });
        let newline;
        while ((newline = buffer.indexOf("\n")) >= 0) {
          const line = buffer.slice(0, newline).trimEnd();
          buffer = buffer.slice(newline + 1);
          if (skipLine) { skipLine = false; continue; }
          if (!line.startsWith("data:")) continue;
          const data = line.slice(5).trim();
          if (!data || data === "[DONE]") continue;
          try {
            const event = JSON.parse(data);
            lastEvent = code(event?.type) ?? "unrecognized_event";
            if (event?.response) {
              reportedModel = code(event.response.model) ?? reportedModel;
              if (Number.isSafeInteger(event.response.max_output_tokens)) {
                reportedOutputLimit = event.response.max_output_tokens;
              }
            }
            if (["response.completed", "response.failed", "response.incomplete", "error"].includes(event?.type)) {
              terminal = event.type;
              errorCode = code(event.error?.code ?? event.response?.error?.code ?? event.code);
              const reported = event.response?.usage;
              usage = reported ? Object.fromEntries(
                ["input_tokens", "output_tokens", "total_tokens"].filter((key) => typeof reported[key] === "number")
                  .map((key) => [key, reported[key]]),
              ) : null;
            }
          } catch (error) {
            if (!(error instanceof SyntaxError)) throw error;
            lastEvent = "unparsed_data";
          }
        }
        if (buffer.length > 1048576) {
          oversizedFrames += 1;
          buffer = "";
          skipLine = true;
        }
        controller.enqueue(result.value);
      },
      async cancel() {
        if (finished) return;
        finished = true;
        try {
          await emit({ phase: "client_cancel", ...summary() });
        } finally {
          await reader.cancel();
          reader.releaseLock();
        }
      },
    }), { status: response.status, statusText: response.statusText, headers: response.headers });
  };
}

export async function diagnosticFetch(url, init) {
  const destination = process.env.COPILOT_GATEWAY_DIAGNOSTIC_LOG;
  if (!destination) throw new Error("COPILOT_GATEWAY_DIAGNOSTIC_LOG is required for diagnostic mode");
  return createDiagnosticFetch(fetch, (event) =>
    appendFile(destination, `${JSON.stringify(event)}\n`, { mode: 0o600 }),
  )(url, init);
}
