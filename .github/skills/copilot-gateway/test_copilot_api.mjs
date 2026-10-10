import assert from "node:assert/strict";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { pathToFileURL } from "node:url";
import { normalizeCatalog } from "./refresh-copilot-api-catalog.mjs";

test("catalog preserves instructions and all models while using authorized per-model limits", () => {
  const models = ["gpt-6-astra", "gpt-6.1-sol"].map((slug) => ({
    slug,
    model_messages: { instructions_template: "upstream instructions" },
    supported_reasoning_levels: [{ effort: "ultra" }],
  }));
  const discovery = { data: models.map((model) => ({
    id: model.slug,
    capabilities: {
      limits: { max_context_window_tokens: 1050000, max_prompt_tokens: 922000, max_output_tokens: 128000 },
      supports: { reasoning_effort: ["low", "high", "xhigh", "max"] },
    },
  })) };
  const result = normalizeCatalog({ models }, discovery);
  assert.deepEqual(result.models.map((model) => model.slug), models.map((model) => model.slug));
  assert.equal(result.models[0].context_window, 1000000);
  assert.equal(result.models[0].auto_compact_token_limit, 784800);
  assert.equal(result.models[1].context_window, 1050000);
  assert.equal(result.models[1].auto_compact_token_limit, 829800);
  assert.deepEqual(result.models[1].supported_reasoning_levels.map((entry) => entry.effort),
    ["low", "high", "xhigh", "max"]);
  assert.equal(result.models[1].model_messages.instructions_template, "upstream instructions");
  assert.throws(() => normalizeCatalog({ models }, { data: [] }), /not advertised/);
});

test("patched direct OAuth preserves authentication without token exchange or editor impersonation", async () => {
  const root = process.env.COPILOT_API_HOME || join(homedir(), ".local/state/copilot-api-gateway");
  const temporary = await mkdtemp(join(tmpdir(), "copilot-api-auth-test-"));
  try {
    await writeFile(join(temporary, "config.json"), '{"providers":{"github-copilot":{"enabled":true}}}', { mode: 0o600 });
    process.env.COPILOT_API_HOME = temporary;
    process.env.COPILOT_API_OAUTH_APP = "copilot-cli";
    const dist = join(root, "runtime/node_modules/@jeffreycao/copilot-api/dist");
    const api = await import(pathToFileURL(join(dist, "token-kSGCOmTG.js")).href);
    const { D: state } = await import(pathToFileURL(join(dist, "config-store-C6GAtKLz.js")).href);
    state.githubToken = "fixture-token";
    await api.c({ getCopilotToken: () => assert.fail("Direct OAuth must not exchange tokens") });
    assert.equal(state.copilotToken, "fixture-token");
    assert.equal(api.y(state), "https://api.githubcopilot.com");
    const headers = api.b(state, "fixture-request", true);
    assert.equal(headers.Authorization, "Bearer fixture-token");
    assert.equal(headers["Copilot-Integration-Id"], "copilot-developer-cli");
    assert.equal(headers["User-Agent"], "copilot-api-gateway/2.7.11");
    assert.equal(headers["X-Request-Id"], "fixture-request");
    assert.equal(headers["Copilot-Vision-Request"], "true");
    assert.equal(headers["editor-version"], undefined);
    assert.equal(headers["x-initiator"], undefined);
    api.E(headers, state);
    assert.equal(headers["User-Agent"], "copilot-api-gateway/2.7.11");
    assert.equal(api.x(state).Authorization, "Bearer fixture-token");
    state.githubToken = undefined;
    await assert.rejects(api.c({}), /OAuth token not found/);
  } finally {
    await rm(temporary, { recursive: true });
  }
});
