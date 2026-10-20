import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { appendFileSync, readFileSync, statSync, unlinkSync, writeFileSync } from "node:fs";
import http from "node:http";
import https from "node:https";
import { join } from "node:path";
import { performance } from "node:perf_hooks";
import { pathToFileURL } from "node:url";
import { createBrotliDecompress, createGunzip, createInflate } from "node:zlib";

const identifier = (value) => typeof value === "string" && /^[a-zA-Z0-9_.-]{1,80}$/.test(value) ? value : null;
export const comparisonHeaders = Object.freeze([
  "accept", "content-type", "user-agent", "copilot-integration-id", "x-request-id",
  "x-initiator", "x-interaction-id", "x-interaction-type", "openai-intent",
  "x-agent-task-id", "x-github-api-version", "copilot-vision-request",
  "x-session-affinity", "x-parent-session-id", "accept-encoding",
]);

export function createSseSummary(onTextDelta) {
  const decoder = new TextDecoder();
  const counts = new Map();
  let buffer = "";
  let skipLine = false;
  let terminal = null;
  let errorCode = null;
  let usage = null;
  let reportedModel = null;
  let reportedOutputLimit = null;
  let malformed = 0;
  let oversized = 0;
  return {
    push(chunk) {
      buffer += decoder.decode(chunk, { stream: true });
      let newline;
      while ((newline = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, newline).trimEnd();
        buffer = buffer.slice(newline + 1);
        if (skipLine) { skipLine = false; continue; }
        if (!line.startsWith("data:")) continue;
        const data = line.slice(5).trim();
        if (!data || data === "[DONE]") continue;
        let event;
        try { event = JSON.parse(data); } catch (error) {
          if (!(error instanceof SyntaxError)) throw error;
          malformed += 1;
          continue;
        }
        const type = identifier(event?.type);
        if (!type) { malformed += 1; continue; }
        if (event.response) {
          reportedModel = identifier(event.response.model) ?? reportedModel;
          if (Number.isSafeInteger(event.response.max_output_tokens)) {
            reportedOutputLimit = event.response.max_output_tokens;
          }
        }
        counts.set(type, (counts.get(type) ?? 0) + 1);
        if (type === "response.output_text.delta" && typeof event.delta === "string") {
          onTextDelta?.(event.delta);
        }
        if (["response.completed", "response.failed", "response.incomplete", "error"].includes(type)) {
          terminal = type;
          errorCode = identifier(event.error?.code ?? event.response?.error?.code ?? event.code);
          const reported = event.response?.usage;
          usage = reported ? Object.fromEntries(
            ["input_tokens", "output_tokens", "total_tokens"]
              .filter((key) => typeof reported[key] === "number")
              .map((key) => [key, reported[key]]),
          ) : null;
        }
      }
      if (buffer.length > 1048576) { oversized += 1; buffer = ""; skipLine = true; }
    },
    snapshot() {
      return {
        event_counts: Object.fromEntries(counts), terminal, error_code: errorCode, usage, malformed, oversized,
        reported_model: reportedModel, reported_max_output_tokens: reportedOutputLimit,
      };
    },
  };
}

function privateBytes(path) {
  const stat = statSync(path);
  assert(stat.isFile() && (stat.mode & 0o077) === 0 && stat.uid === process.getuid(), `Expected private owned file: ${path}`);
  return readFileSync(path);
}

export async function runProbe(target, directory, gatewayPort = 4004) {
  assert(["gateway", "direct"].includes(target), "Target must be gateway or direct");
  assert(Number.isSafeInteger(gatewayPort) && gatewayPort > 0 && gatewayPort <= 65535, "Invalid loopback port");
  const direct = target === "direct";
  const body = privateBytes(join(directory, direct ? "upstream-request.private.body" : "client-request.private.json"));
  const token = privateBytes(join(directory, direct ? "copilot-cli/github_token" : "proxy-key")).toString().trim();
  const captured = direct ? JSON.parse(privateBytes(join(directory, "upstream-headers.private.json"))) : {
    "content-type": "application/json", "user-agent": "codex/0.159.3",
  };
  const publicHeaders = Object.fromEntries(Object.entries(captured).filter(([name]) => comparisonHeaders.includes(name.toLowerCase())));
  const headers = { ...publicHeaders, Authorization: `Bearer ${token}`, "Content-Length": String(body.length) };
  const destination = direct ? "https://api.githubcopilot.com/responses" : `http://127.0.0.1:${gatewayPort}/v1/responses`;
  const log = join(directory, `${target}-socket-events.jsonl`);
  const started = performance.now();
  const elapsed = () => Math.round(performance.now() - started);
  const record = (phase, details = {}) => appendFileSync(
    log, `${JSON.stringify({ at: new Date().toISOString(), elapsed_ms: elapsed(), phase, ...details })}\n`, { mode: 0o600 },
  );
  const sse = createSseSummary();
  const state = {
    target, request_body_sha256: createHash("sha256").update(body).digest("hex"), request_body_bytes: body.length,
    status: null, headers: {}, raw_body_bytes: 0, first_byte_ms: null, last_byte_ms: null,
    upload_finished_ms: null, peer_end_observed: false, socket_closed: false, probe_aborted: false,
    http_complete: false, transport_error_code: null,
  };
  record("request", { target, body_sha256: state.request_body_sha256, bytes: body.length, header_names: Object.keys(publicHeaders) });
  const agent = direct ? new https.Agent({ keepAlive: true, maxSockets: 1 }) : new http.Agent({ keepAlive: true, maxSockets: 1 });
  return await new Promise((resolve) => {
    let finishing = false;
    const finish = (outcome) => {
      if (finishing) return;
      finishing = true;
      clearTimeout(deadline);
      setTimeout(() => {
        const result = { ...state, elapsed_ms: elapsed(), outcome, ...sse.snapshot(), tools_executed: false };
        writeFileSync(join(directory, `${target}-result.json`), `${JSON.stringify(result, null, 2)}\n`, { mode: 0o600 });
        record("result", result);
        console.log(JSON.stringify(result));
        agent.destroy();
        resolve(result);
      }, 25);
    };
    const request = (direct ? https : http).request(destination, { method: "POST", headers, agent }, (response) => {
      state.status = response.statusCode;
      state.headers = Object.fromEntries(
        ["x-github-request-id", "x-copilot-service-request-id", "x-request-id", "content-type", "transfer-encoding", "content-encoding"]
          .filter((name) => response.headers[name] !== undefined).map((name) => [name, response.headers[name]]),
      );
      record("headers", { status: response.statusCode, http_version: response.httpVersion, headers: state.headers });
      response.on("data", (chunk) => {
        const time = elapsed();
        if (state.first_byte_ms === null) { state.first_byte_ms = time; record("first_byte"); }
        state.last_byte_ms = time;
        state.raw_body_bytes += chunk.length;
      });
      const encoding = response.headers["content-encoding"];
      const unpack = encoding === "gzip" ? createGunzip()
        : encoding === "deflate" ? createInflate()
          : encoding === "br" ? createBrotliDecompress() : null;
      assert(!encoding || encoding === "identity" || unpack, "Unsupported response encoding");
      const decoded = unpack ? response.pipe(unpack) : response;
      decoded.on("data", (chunk) => sse.push(chunk));
      decoded.on("end", () => { state.http_complete = response.complete; record("response_end", { complete: response.complete }); finish("response_end"); });
      if (unpack) unpack.on("error", (error) => { record("decompression_error", { code: identifier(error.code) }); finish("decompression_error"); });
      response.on("aborted", () => { record("response_aborted", { complete: response.complete }); finish("response_aborted"); });
      response.on("error", (error) => { state.transport_error_code = identifier(error.code); record("response_error", { code: state.transport_error_code, name: error.name }); finish("response_error"); });
    });
    const deadline = setTimeout(() => {
      state.probe_aborted = true;
      record("probe_deadline");
      request.destroy(new Error("Diagnostic 1200-second deadline"));
      finish("probe_deadline");
    }, 1200000);
    request.on("socket", (socket) => {
      record("socket_assigned");
      socket.on("connect", () => record("socket_connected", { local_port: socket.localPort, remote_address: socket.remoteAddress }));
      socket.on("secureConnect", () => record("tls_connected", { authorized: socket.authorized, protocol: socket.getProtocol(), peer_common_name: socket.getPeerCertificate().subject?.CN }));
      socket.on("end", () => { state.peer_end_observed = true; record("socket_peer_end", { probe_aborted: state.probe_aborted }); });
      socket.on("error", (error) => record("socket_error", { code: identifier(error.code), name: error.name }));
      socket.on("close", (hadError) => {
        state.socket_closed = true;
        record("socket_close", { had_error: hadError, probe_aborted: state.probe_aborted, bytes_read: socket.bytesRead, bytes_written: socket.bytesWritten });
      });
    });
    request.on("finish", () => { state.upload_finished_ms = elapsed(); record("upload_finished"); });
    request.on("error", (error) => { state.transport_error_code = identifier(error.code); record("request_error", { code: state.transport_error_code, name: error.name }); finish("request_error"); });
    request.end(body);
  });
}

export async function runCurlProbe(directory, onTextDelta) {
  const bodyPath = join(directory, "upstream-request.private.body");
  const body = privateBytes(bodyPath);
  const token = privateBytes(join(directory, "copilot-cli/github_token")).toString().trim();
  const captured = JSON.parse(privateBytes(join(directory, "upstream-headers.private.json")));
  const entries = Object.entries(captured).filter(([name]) => comparisonHeaders.includes(name.toLowerCase()));
  assert(entries.every(([name, value]) => !/[\r\n]/.test(name + value)), "Invalid captured headers");
  const headerPath = join(directory, "curl-request.private.headers");
  const responseHeadersPath = join(directory, "curl-response.private.headers");
  writeFileSync(headerPath, [...entries, ["authorization", `Bearer ${token}`]].map(
    ([name, value]) => `${name}: ${value}\n`,
  ).join(""), { mode: 0o600, flag: "wx" });
  const timingFormat = '%{stderr}\nDIAG_RESULT {"http_code":%{http_code},"time_total":%{time_total},'
    + '"time_starttransfer":%{time_starttransfer},"time_connect":%{time_connect},'
    + '"size_upload":%{size_upload},"size_download":%{size_download},"exit_code":%{exitcode},'
    + '"http_version":"%{http_version}"}\n';
  const sse = createSseSummary(onTextDelta);
  const started = performance.now();
  let firstDataMs = null;
  let lastDataMs = null;
  let stderr = "";
  try {
    const child = spawn("curl", [
      "--http1.1", "--no-buffer", "--silent", "--show-error", "--connect-timeout", "20",
      "--max-time", "1200", "--noproxy", "*", "--proto", "=https", "--header", "Expect:",
      "--header", `@${headerPath}`, "--data-binary", `@${bodyPath}`,
      "--dump-header", responseHeadersPath, "--write-out", timingFormat,
      "https://api.githubcopilot.com/responses",
    ], { stdio: ["ignore", "pipe", "pipe"] });
    child.stdout.on("data", (chunk) => {
      firstDataMs ??= Math.round(performance.now() - started);
      lastDataMs = Math.round(performance.now() - started);
      sse.push(chunk);
    });
    child.stderr.on("data", (chunk) => { stderr += chunk.toString(); });
    const exit = await new Promise((resolve, reject) => {
      child.once("error", reject);
      child.once("close", resolve);
    });
    const timingLine = stderr.split("\n").find((line) => line.startsWith("DIAG_RESULT "));
    assert(timingLine, "curl did not produce transfer metrics");
    const headers = Object.fromEntries(readFileSync(responseHeadersPath, "utf8").split(/\r?\n/).flatMap((line) => {
      const separator = line.indexOf(":");
      if (separator < 0) return [];
      const name = line.slice(0, separator).toLowerCase();
      return ["x-github-request-id", "x-copilot-service-request-id", "content-type", "content-encoding",
        "transfer-encoding", "date"].includes(name) ? [[name, line.slice(separator + 1).trim()]] : [];
    }));
    const result = {
      target: "curl", exit, timing: JSON.parse(timingLine.slice("DIAG_RESULT ".length)),
      first_byte_ms: firstDataMs, last_byte_ms: lastDataMs, probe_aborted: exit === 28,
      request_body_sha256: createHash("sha256").update(body).digest("hex"),
      headers, ...sse.snapshot(), tools_executed: false,
    };
    writeFileSync(join(directory, "curl-result.json"), `${JSON.stringify(result, null, 2)}\n`, { mode: 0o600 });
    writeFileSync(join(directory, "curl-stderr.log"), stderr, { mode: 0o600 });
    console.log(JSON.stringify(result));
    return result;
  } finally {
    unlinkSync(headerPath);
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  assert(process.argv.length === 4, "Usage: node probe-copilot-http.mjs gateway|direct|curl PRIVATE_STATE_DIRECTORY");
  const result = process.argv[2] === "curl"
    ? await runCurlProbe(process.argv[3])
    : await runProbe(process.argv[2], process.argv[3]);
  process.exitCode = result.terminal === "response.completed" && !result.probe_aborted
    && (result.status ?? result.timing?.http_code) === 200 ? 0 : 1;
}
