import assert from "node:assert/strict";
import { readFile, rename, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

export function normalizeCatalog(catalog, discovery) {
  assert(Array.isArray(catalog.models) && catalog.models.length > 0, "Empty Codex catalog");
  assert(Array.isArray(discovery.data), "Invalid model discovery");
  const available = new Map(discovery.data.map((model) => [model.id, model]));
  return {
    ...catalog,
    models: catalog.models.map((model) => {
      const upstream = available.get(model.slug);
      assert(upstream, `Catalog model is not advertised: ${model.slug}`);
      const { limits, supports } = upstream.capabilities;
      const context = model.slug === "gpt-6-astra"
        ? Math.min(1000000, limits.max_context_window_tokens)
        : limits.max_context_window_tokens;
      const input = Math.min(limits.max_prompt_tokens, context - limits.max_output_tokens);
      assert(Number.isSafeInteger(context) && Number.isSafeInteger(input) && input > 0,
        `Invalid context limits: ${model.slug}`);
      const efforts = supports.reasoning_effort;
      assert(Array.isArray(efforts), `Missing reasoning capabilities: ${model.slug}`);
      return {
        ...model,
        context_window: context,
        max_context_window: context,
        auto_compact_token_limit: Math.floor(input * 0.9),
        effective_context_window_percent: Math.floor(input * 100 / context),
        default_reasoning_level: efforts.includes("high") ? "high" : efforts[0] ?? "none",
        supported_reasoning_levels: efforts.map((effort) => ({ effort, description: `${effort} reasoning` })),
      };
    }),
  };
}

async function main() {
  const state = process.env.COPILOT_API_HOME || join(homedir(), ".local/state/copilot-api-gateway");
  const key = (await readFile(join(state, "proxy-key"), "utf8")).trim();
  const headers = { Authorization: `Bearer ${key}`, Accept: "application/json" };
  const fetchJson = async (path, extraHeaders = {}) => {
    const response = await fetch(`http://127.0.0.1:4000${path}`, {
      headers: { ...headers, ...extraHeaders },
      redirect: "error",
      signal: AbortSignal.timeout(120000),
    });
    assert(response.ok, `Catalog request failed: HTTP ${response.status}`);
    return response.json();
  };
  const [catalog, discovery] = await Promise.all([
    fetchJson("/models?client_version=0.159.3", {
      "User-Agent": "codex/0.159.3",
      version: "0.159.3",
      "x-full-model-catalog": "true",
    }),
    fetchJson("/v1/models"),
  ]);
  const normalized = normalizeCatalog(catalog, discovery);
  const destination = join(state, "codex-model-catalog.json");
  const temporary = `${destination}.${process.pid}.tmp`;
  await writeFile(temporary, `${JSON.stringify(normalized, null, 2)}\n`, { mode: 0o600, flag: "wx" });
  await rename(temporary, destination);
  console.log(`Saved ${normalized.models.length} available coding models to ${destination}`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  await main();
}
