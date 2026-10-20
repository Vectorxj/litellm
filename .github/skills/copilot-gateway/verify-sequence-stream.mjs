import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { closeSync, openSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { runCurlProbe } from "./probe-copilot-http.mjs";

export const sequencePrompt = (count) => `This is a finite streaming transport test. Return a JSON object with rows containing exactly the integers 1 through ${count} in ascending order, each exactly once. Do not summarize, skip entries, provide code, or call tools. Produce the requested data directly.`;

export function analyzeSequence(text, expectedCount) {
  let parsed;
  let jsonValid = false;
  try {
    parsed = JSON.parse(text);
    jsonValid = true;
  } catch (error) {
    if (!(error instanceof SyntaxError)) throw error;
  }
  const rows = Array.isArray(parsed?.rows) ? parsed.rows : null;
  const opening = /^\s*\{\s*"rows"\s*:\s*\[/.exec(text);
  const remaining = opening ? text.slice(opening[0].length) : "";
  const number = /\s*(-?\d+)\s*([,\]])/gy;
  let delimited = 0;
  let lastNumber = null;
  let firstMismatch = null;
  let contiguousPrefix = 0;
  let arrayClosed = false;
  let consumed = 0;
  let match;
  while (opening && (match = number.exec(remaining)) !== null) {
    const value = Number(match[1]);
    delimited += 1;
    lastNumber = value;
    if (firstMismatch === null && value !== delimited) {
      firstMismatch = { position: delimited, expected: delimited, observed: value };
    }
    if (firstMismatch === null) contiguousPrefix = delimited;
    consumed = number.lastIndex;
    if (match[2] === "]") { arrayClosed = true; break; }
  }
  return {
    expected_count: expectedCount,
    text_bytes: Buffer.byteLength(text),
    text_sha256: createHash("sha256").update(text).digest("hex"),
    json_valid: jsonValid,
    row_count: rows?.length ?? null,
    exact_sequence: rows !== null && rows.length === expectedCount && rows.every((value, index) => value === index + 1),
    delimited_numbers: delimited,
    contiguous_prefix: contiguousPrefix,
    last_delimited_number: lastNumber,
    first_mismatch: firstMismatch,
    array_closed: arrayClosed,
    trailing_fragment: opening ? remaining.slice(consumed, consumed + 120) : null,
    tail: text.slice(-160),
  };
}

export async function verifySequence(directory, probe = runCurlProbe) {
  const request = JSON.parse(readFileSync(join(directory, "upstream-request.private.body"), "utf8"));
  const allowed = new Set(["model", "stream", "store", "reasoning", "max_output_tokens", "input", "text"]);
  assert(Object.keys(request).every((key) => allowed.has(key)), "Synthetic capture cannot include other context fields");
  const count = request.text?.format?.schema?.properties?.rows?.minItems;
  assert(Number.isSafeInteger(count) && count > 0, "Expected a finite sequence request");
  assert(request.input === sequencePrompt(count), "Text capture is restricted to the synthetic numeric prompt");
  assert(request.text.format.schema.properties.rows.maxItems === count, "Expected a fixed sequence length");
  const textPath = join(directory, "output-text.txt");
  const fd = openSync(textPath, "wx", 0o600);
  let protocol = null;
  let analysis;
  try {
    protocol = await probe(directory, (delta) => writeFileSync(fd, delta));
  } finally {
    closeSync(fd);
    analysis = {
      ...analyzeSequence(readFileSync(textPath, "utf8"), count),
      protocol_terminal: protocol?.terminal ?? null,
      reported_model: protocol?.reported_model ?? null,
    };
    writeFileSync(join(directory, "content-analysis.json"), `${JSON.stringify(analysis, null, 2)}\n`, { mode: 0o600 });
    console.log(JSON.stringify({ content_analysis: analysis }));
  }
  return { protocol, analysis };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  assert(process.argv.length === 3, "Usage: node verify-sequence-stream.mjs PRIVATE_CASE_DIRECTORY");
  const { protocol, analysis } = await verifySequence(process.argv[2]);
  process.exitCode = protocol.exit === 0 && protocol.timing.http_code === 200 && !protocol.probe_aborted
    && protocol.terminal === "response.completed" && analysis.exact_sequence ? 0 : 1;
}
