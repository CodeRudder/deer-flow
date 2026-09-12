/**
 * Tests for the settings dialog's managed-model section
 * (`src/components/workspace/settings/model-settings-page.tsx`).
 *
 * The page reads the admin-only `GET /api/models/config` region through
 * `useManagedModels`. A non-admin session gets a 403 that the API client
 * surfaces as `ModelConfigRequestError.isAdminRequired` — the page must turn
 * that into the "administrator required" copy, never render a raw error stack.
 * That gating branch is the failure-prone piece pinned here; the rest of the
 * suite covers the pure payload helpers (the single-key-field rule, which keeps
 * cleartexts out of config.yaml) and row listing.
 */
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

// jsdom does not implement scrollIntoView, which Radix Select calls when it
// opens. Same shim as the other Radix-Select unit tests.
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => undefined;
}

import {
  buildManagedModelProbe,
  buildManagedModelWrite,
  deriveApiKeyVarName,
  EMPTY_MODEL_DRAFT,
  draftProviderFor,
  isManagedModelDraftComplete,
  type ManagedModelDraft,
  maskedKeyLabel,
  ModelSettingsPage,
  readThinkingDisabledMode,
} from "@/components/workspace/settings/model-settings-page";
import { ModelConfigRequestError } from "@/core/models/api";
import type {
  ManagedModel,
  ManagedModelWrite,
  ModelProvider,
} from "@/core/models/types";

// The page imports the admin API client (fetcher/config) at module scope; stub
// the network layer so the import never reaches for a real backend.
rs.mock("@/core/api/fetcher", () => ({ fetch: rs.fn() }));
rs.mock("@/core/config", () => ({ getBackendBaseURL: () => "" }));

const modelsState = rs.hoisted(() => ({
  current: {
    models: [] as ManagedModel[],
    isLoading: false,
    error: undefined as unknown,
  },
}));

// Drive the probe hook's pending state from a test: the "still waiting" copy is
// only reachable while a probe is in flight, and a probe takes 20-30s of real
// network time, so it cannot be waited out in a unit test.
const probeState = rs.hoisted(() => ({ pending: false }));

rs.mock("@/core/models/hooks", () => ({
  useManagedModels: () => modelsState.current,
  useModelProviders: () => ({ providers: PROVIDERS, isLoading: false, error: undefined }),
  useSaveManagedModel: () => ({ mutate: rs.fn(), isPending: false }),
  useDeleteManagedModel: () => ({ mutate: rs.fn(), isPending: false }),
  useTestManagedModel: () => ({ mutate: rs.fn(), isPending: false }),
  useProbeManagedModelThinking: () => ({ mutate: rs.fn(), isPending: probeState.pending }),
}));

rs.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    t: {
      common: { loading: "加载中…" },
      settings: {
        models: {
          title: "模型",
          description: "管理可用的语言模型配置。",
          adminRequired: "需要管理员权限才能管理模型配置。",
          empty: "暂无模型配置。",
          add: "添加模型",
          edit: "编辑",
          editTitle: "编辑模型",
          addTitle: "添加模型",
          delete: "删除",
          deleteConfirmTitle: "删除模型",
          deleteConfirm: "确定要删除 {name} 吗？",
          test: "测试连接",
          testOk: "连接成功（{latency} ms）",
          testFailed: "连接失败：{detail}",
          save: "保存",
          saving: "保存中…",
          cancel: "取消",
          name: "名称",
          namePlaceholder: "例如 gpt-4o",
          use: "提供方类路径",
          usePlaceholder: "langchain_openai:ChatOpenAI",
          model: "模型",
          modelPlaceholder: "gpt-4o",
          displayName: "显示名称",
          displayNamePlaceholder: "GPT-4o",
          apiKey: "API Key",
          apiKeyPlaceholder: "sk-... 或 $OPENAI_API_KEY",
          apiKeyStoredAsHint: "密钥将保存到 .env 的 {name}。",
          apiKeyKeepHint: "留空则保留已保存的密钥。",
          required: "名称、提供方类路径与模型均为必填项。",
          keyNotSet: "未设置",
          saveSuccess: "模型已保存。",
          deleteSuccess: "模型已删除。",
          loadError: "加载失败：{detail}",
          supportsThinking: "该模型支持思考",
          supportsThinkingHint:
            "勾选后会写入该服务商的思考参数。不勾选时，对话界面的思考开关对本模型无效。",
          thinkingUnavailable: "该服务商不支持思考参数，无法开启。",
          budgetTokens: "思考预算 budget_tokens",
          budgetHint: "思考内容的上限，默认 4096。",
          thinkingDisabledTitle: "关闭思考时发送",
          thinkingDisabledHint:
            "关闭思考时带上该服务商自己的禁用参数（如 thinking.type = disabled）。",
          thinkingDisabledPreset: "发送服务商的禁用参数",
          thinkingDisabledOmit: "不发送任何禁用参数 —— 该端点不接受禁用信号",
          maxTokens: "最大输出 max_tokens",
          maxTokensHint: "须大于思考预算，默认 8192。",
          probeThinking: "检测",
          thinkNotProbed: "未检测 — 建议先点「检测」。",
          thinkProbing: "检测中… 正在发送 3 到 5 次真实请求。",
          thinkAlwaysOn: "该端点不传思考参数也会思考，思考始终开启。",
        },
      },
    },
  }),
}));

afterEach(() => {
  cleanup();
  modelsState.current = { models: [], isLoading: false, error: undefined };
  probeState.pending = false;
});

function managedModel(overrides: Partial<ManagedModel> = {}): ManagedModel {
  return {
    index: 0,
    name: "gpt-4o",
    model: "gpt-4o",
    use: "langchain_openai:ChatOpenAI",
    ...overrides,
  };
}

describe("ModelSettingsPage admin gating", () => {
  it("renders the admin-required message for a 403, not the error text", () => {
    modelsState.current = {
      models: [],
      isLoading: false,
      error: new ModelConfigRequestError(403, "admin only"),
    };

    render(<ModelSettingsPage />);

    expect(screen.getByText("需要管理员权限才能管理模型配置。")).toBeTruthy();
    expect(screen.queryByText(/admin only/)).toBeNull();
  });

  it("surfaces a non-403 failure as its detail message", () => {
    modelsState.current = {
      models: [],
      isLoading: false,
      error: new ModelConfigRequestError(500, "config.yaml unreadable"),
    };

    render(<ModelSettingsPage />);

    expect(screen.getByText(/config\.yaml unreadable/)).toBeTruthy();
    expect(screen.queryByText("需要管理员权限才能管理模型配置。")).toBeNull();
  });

  it("lists managed entries once the query resolves", () => {
    modelsState.current = {
      models: [
        managedModel({
          api_key_masked: "sk-****abcd",
          display_name: "GPT-4o",
        }),
      ],
      isLoading: false,
      error: undefined,
    };

    render(<ModelSettingsPage />);

    expect(screen.getByText("gpt-4o")).toBeTruthy();
    expect(screen.getByText("GPT-4o")).toBeTruthy();
    expect(screen.getByText("langchain_openai:ChatOpenAI")).toBeTruthy();
    // The masked key shares one text node with the "API key:" label.
    expect(screen.getByText(/sk-\*\*\*\*abcd/)).toBeTruthy();
  });
});

describe("thinking probe progress", () => {
  async function openProbePanel() {
    modelsState.current = {
      models: [managedModel({ use: "langchain_anthropic:ChatAnthropic" })],
      isLoading: false,
      error: undefined,
    };
    render(<ModelSettingsPage />);
    (await screen.findByRole("button", { name: "编辑" })).click();
    const checkbox = await screen.findByRole("checkbox");
    if (!(checkbox as HTMLInputElement).checked) {
      (checkbox as HTMLInputElement).click();
    }
  }

  it("shows progress instead of the stale verdict while a probe is in flight", async () => {
    // Regression: the panel kept showing "Not detected yet" for the whole 20-30s
    // wait, so the button read as unresponsive and the verdict then appeared all
    // at once. The wait has to be visible.
    probeState.pending = true;
    await openProbePanel();

    expect(screen.getByText(/检测中/)).toBeTruthy();
    expect(screen.queryByText(/未检测/)).toBeNull();
  });

  it("falls back to the not-detected hint once the probe settles", async () => {
    probeState.pending = false;
    await openProbePanel();

    expect(screen.getByText(/未检测/)).toBeTruthy();
    expect(screen.queryByText(/检测中/)).toBeNull();
  });
});

describe("maskedKeyLabel", () => {
  it("prefers a masked string carried on api_key_masked", () => {
    expect(
      maskedKeyLabel(managedModel({ api_key_masked: "sk-****abcd" })),
    ).toBe("sk-****abcd");
  });

  it("falls back to the masked api_key when api_key_masked is a flag", () => {
    expect(
      maskedKeyLabel(
        managedModel({ api_key_masked: true, api_key: "sk-****9" }),
      ),
    ).toBe("sk-****9");
  });

  it("shows a $VAR reference verbatim", () => {
    expect(
      maskedKeyLabel(
        managedModel({ api_key: "$OPENAI_API_KEY", api_key_masked: false }),
      ),
    ).toBe("$OPENAI_API_KEY");
  });

  it("returns null when no key is configured", () => {
    expect(maskedKeyLabel(managedModel())).toBeNull();
  });
});

/** Minimal preset table mirroring the backend's `/api/models/providers`. */
const PROVIDERS: ModelProvider[] = [
  {
    key: "openai",
    label: "OpenAI",
    use: "langchain_openai:ChatOpenAI",
    api_base_field: "openai_api_base",
    default_api_base: null,
    supports_thinking: true,
    thinking_enabled: { extra_body: { thinking: { type: "enabled" } } },
    thinking_disabled: { extra_body: { thinking: { type: "disabled" } } },
    thinking_needs_budget: false,
    default_budget_tokens: 4096,
    available: true,
    reason: null,
  },
  {
    key: "openai-compatible",
    label: "OpenAI-compatible",
    use: "langchain_openai:ChatOpenAI",
    api_base_field: "openai_api_base",
    default_api_base: null,
    supports_thinking: true,
    thinking_enabled: { extra_body: { thinking: { type: "enabled" } } },
    thinking_disabled: { extra_body: { thinking: { type: "disabled" } } },
    thinking_needs_budget: false,
    default_budget_tokens: 4096,
    available: true,
    reason: null,
  },
  {
    key: "doubao",
    label: "Doubao",
    use: "deerflow.models.patched_deepseek:PatchedChatDeepSeek",
    api_base_field: "api_base",
    default_api_base: "https://ark.cn-beijing.volces.com/api/v3",
    supports_thinking: true,
    thinking_enabled: { extra_body: { thinking: { type: "enabled" } } },
    thinking_disabled: { extra_body: { thinking: { type: "disabled" } } },
    thinking_needs_budget: false,
    default_budget_tokens: 4096,
    available: true,
    reason: null,
  },
  {
    // Anthropic is the only preset whose thinking block carries a budget, so it
    // is the one that exercises `withBudget`/`readBudget` end to end.
    key: "anthropic",
    label: "Anthropic Claude",
    use: "langchain_anthropic:ChatAnthropic",
    api_base_field: "anthropic_api_url",
    default_api_base: null,
    supports_thinking: true,
    thinking_enabled: { thinking: { type: "enabled", budget_tokens: 4096 } },
    thinking_disabled: { thinking: { type: "disabled" } },
    thinking_needs_budget: true,
    default_budget_tokens: 4096,
    available: true,
    reason: null,
  },
];

/** Test-local shim: the provider table is fixed here, so bind it once. */
const buildWrite = (
  draft: ManagedModelDraft,
  original?: ManagedModel,
): ManagedModelWrite => buildManagedModelWrite(draft, PROVIDERS, original);


describe("isManagedModelDraftComplete", () => {
  it("requires name, use and model", () => {
    expect(
      isManagedModelDraftComplete({
        ...EMPTY_MODEL_DRAFT,
        name: "gpt-4o",
        provider: "openai",
        model: "gpt-4o",
      }),
    ).toBe(true);
    expect(
      isManagedModelDraftComplete({ ...EMPTY_MODEL_DRAFT, name: "gpt-4o" }),
    ).toBe(false);
    expect(isManagedModelDraftComplete(EMPTY_MODEL_DRAFT)).toBe(false);
  });

  it("treats whitespace-only input as missing", () => {
    expect(
      isManagedModelDraftComplete({
        ...EMPTY_MODEL_DRAFT,
        name: " ",
        provider: " openai ",
        model: "\t",
      }),
    ).toBe(false);
  });
});

describe("deriveApiKeyVarName", () => {
  it("derives an env var name from the entry name", () => {
    expect(deriveApiKeyVarName("doubao-seed-1.8")).toBe(
      "DOUBAO_SEED_1_8_API_KEY",
    );
    expect(deriveApiKeyVarName("gpt-4o")).toBe("GPT_4O_API_KEY");
    expect(deriveApiKeyVarName("DeepSeek_V3")).toBe("DEEPSEEK_V3_API_KEY");
  });

  it("collapses runs of separators instead of repeating underscores", () => {
    expect(deriveApiKeyVarName("my  model/v2")).toBe("MY_MODEL_V2_API_KEY");
    expect(deriveApiKeyVarName("--edge--")).toBe("EDGE_API_KEY");
  });

  it("never starts with a digit", () => {
    const derived = deriveApiKeyVarName("1.8-model");
    expect(derived).toBe("_1_8_MODEL_API_KEY");
    expect(/^[0-9]/.test(derived)).toBe(false);
  });

  it("falls back to a stable name when nothing usable survives", () => {
    expect(deriveApiKeyVarName("模型")).toBe("MODEL_API_KEY");
    expect(deriveApiKeyVarName("   ")).toBe("MODEL_API_KEY");
  });

  it("only ever yields a POSIX-ish env name", () => {
    for (const name of [
      "doubao-seed-1.8",
      "gpt-4o",
      "1.8-model",
      "模型",
      "my  model/v2",
      "--edge--",
      "a.b:c d",
    ]) {
      expect(deriveApiKeyVarName(name)).toMatch(/^[A-Z_][A-Z0-9_]*$/);
    }
  });
});

describe("buildManagedModelWrite (single key field)", () => {
  const complete = {
    ...EMPTY_MODEL_DRAFT,
    name: "gpt-4o",
    provider: "openai",
    model: "gpt-4o",
  };

  it("trims the required fields", () => {
    const write = buildWrite({
      ...complete,
      name: " gpt-4o ",
      model: " gpt-4o ",
    });
    expect(write.name).toBe("gpt-4o");
    expect(write.model).toBe("gpt-4o");
  });

  it("omits both key fields when the user did not retype a key", () => {
    const write = buildWrite(complete);
    expect(write).not.toHaveProperty("api_key");
    expect(write).not.toHaveProperty("api_key_value");
  });

  it("does not prefill a key when editing an entry with a stored secret", () => {
    const write = buildWrite(complete, {
      index: 0,
      name: "gpt-4o",
      model: "gpt-4o",
      use: "langchain_openai:ChatOpenAI",
      api_key: "sk-live-1234",
      api_key_masked: "sk-****1234",
    });
    expect(write).not.toHaveProperty("api_key");
    expect(write).not.toHaveProperty("api_key_value");
  });

  it("stores a typed literal in .env behind a derived name, never in config.yaml", () => {
    const write = buildWrite({
      ...complete,
      name: "doubao-seed-1.8",
      api_key: "sk-live-1234",
    });
    expect(write.api_key).toBe("$DOUBAO_SEED_1_8_API_KEY");
    expect(write.api_key_value).toBe("sk-live-1234");
    expect(write.api_key).not.toContain("sk-live-1234");
  });

  it("derives the name from the edited entry's name", () => {
    const write = buildWrite(
      { ...complete, name: "renamed-entry", api_key: "sk-live-1234" },
      {
        index: 0,
        name: "gpt-4o",
        model: "gpt-4o",
        use: "langchain_openai:ChatOpenAI",
      },
    );
    expect(write.api_key).toBe("$RENAMED_ENTRY_API_KEY");
  });

  it("passes a typed $VAR reference through untouched, with no cleartext", () => {
    const write = buildWrite({
      ...complete,
      api_key: "$BRAND_NEW_KEY",
    });
    expect(write.api_key).toBe("$BRAND_NEW_KEY");
    expect(write).not.toHaveProperty("api_key_value");
  });

  it("keeps provider-specific extras an edit does not expose", () => {
    const write = buildWrite(complete, {
      index: 2,
      name: "gpt-4o",
      model: "gpt-4o",
      use: "langchain_openai:ChatOpenAI",
      when_thinking_enabled: { extra_body: { thinking: { type: "enabled" } } },
      max_tokens: 4096,
      api_key_masked: true,
    });
    expect(write.when_thinking_enabled).toEqual({
      extra_body: { thinking: { type: "enabled" } },
    });
    expect(write.max_tokens).toBe(4096);
    // Server-owned addressing metadata never round-trips into the file.
    expect(write).not.toHaveProperty("index");
    expect(write).not.toHaveProperty("api_key_masked");
  });

  it("normalizes a blank display_name to null", () => {
    expect(buildWrite(complete).display_name).toBeNull();
    expect(
      buildWrite({ ...complete, display_name: " GPT-4o " })
        .display_name,
    ).toBe("GPT-4o");
  });
});

describe("buildManagedModelWrite (thinking)", () => {
  const anthropic = {
    ...EMPTY_MODEL_DRAFT,
    name: "claude-sonnet",
    provider: "anthropic",
    model: "claude-sonnet-5",
  };
  const openai = {
    ...EMPTY_MODEL_DRAFT,
    name: "gpt-4o",
    provider: "openai",
    model: "gpt-4o",
  };
  /** The stored entry `buildWrite`'s `original` argument expects. */
  const storedEntry = (overrides: Partial<ManagedModel> = {}): ManagedModel => ({
    index: 0,
    name: "gpt-4o",
    model: "gpt-4o",
    use: "langchain_openai:ChatOpenAI",
    ...overrides,
  });

  it("writes the provider's own block shape, not a synthesised one", () => {
    const write = buildWrite({
      ...anthropic,
      supports_thinking: true,
      budget_tokens: "",
    });
    expect(write.supports_thinking).toBe(true);
    // Anthropic nests the budget; an OpenAI-compatible preset must not get this.
    expect(write.when_thinking_enabled).toEqual({
      thinking: { type: "enabled", budget_tokens: 4096 },
    });
    expect(write.when_thinking_disabled).toEqual({
      thinking: { type: "disabled" },
    });
  });

  it("substitutes the typed budget without touching the rest of the block", () => {
    const write = buildWrite({
      ...anthropic,
      supports_thinking: true,
      budget_tokens: "2048",
    });
    expect(write.when_thinking_enabled).toEqual({
      thinking: { type: "enabled", budget_tokens: 2048 },
    });
  });

  it("ignores a blank or non-numeric budget instead of writing NaN", () => {
    for (const budget_tokens of ["", "  ", "abc", "0", "-5"]) {
      const write = buildWrite({
        ...anthropic,
        supports_thinking: true,
        budget_tokens,
      });
      expect(write.when_thinking_enabled).toEqual({
        thinking: { type: "enabled", budget_tokens: 4096 },
      });
    }
  });

  it("writes the OpenAI-compatible shape under extra_body", () => {
    const write = buildWrite({ ...openai, supports_thinking: true });
    expect(write.when_thinking_enabled).toEqual({
      extra_body: { thinking: { type: "enabled" } },
    });
  });

  it("writes no thinking keys on a create that never enabled it", () => {
    const write = buildWrite(openai);
    expect(write).not.toHaveProperty("supports_thinking");
    expect(write).not.toHaveProperty("when_thinking_enabled");
    expect(write).not.toHaveProperty("when_thinking_disabled");
  });

  it("clears every thinking key when the box is unchecked on a think-enabled entry", () => {
    const write = buildWrite(
      { ...openai, supports_thinking: false },
      storedEntry({
        supports_thinking: true,
        when_thinking_enabled: { extra_body: { thinking: { type: "enabled" } } },
        when_thinking_disabled: { extra_body: { thinking: { type: "disabled" } } },
        max_tokens: 8192,
      }),
    );
    // `supports_thinking` is the flag DeerFlow reads, so it must not survive.
    expect(write).not.toHaveProperty("supports_thinking");
    expect(write).not.toHaveProperty("when_thinking_enabled");
    expect(write).not.toHaveProperty("when_thinking_disabled");
    expect(write).not.toHaveProperty("max_tokens");
  });

  it("keeps a hand-written thinking block the form cannot express", () => {
    const write = buildWrite(
      openai,
      storedEntry({
        when_thinking_enabled: { extra_body: { thinking: { type: "enabled" } } },
        max_tokens: 4096,
      }),
    );
    // Never enabled by the form, so the entry is left exactly as stored.
    expect(write.when_thinking_enabled).toEqual({
      extra_body: { thinking: { type: "enabled" } },
    });
    expect(write.max_tokens).toBe(4096);
  });

  it("drops the legacy top-level thinking alias when the form takes over", () => {
    const write = buildWrite(
      { ...openai, supports_thinking: true },
      storedEntry({
        thinking: { type: "enabled", budget_tokens: 1024 },
        supports_thinking: true,
      }),
    );
    expect(write).not.toHaveProperty("thinking");
    expect(write.when_thinking_enabled).toEqual({
      extra_body: { thinking: { type: "enabled" } },
    });
  });

  it("defaults to the provider's disable block, so stored entries keep working", () => {
    // The pre-existing behaviour: the mode must not change unless asked.
    expect(EMPTY_MODEL_DRAFT.thinking_disabled).toBe("preset");
    const write = buildWrite({ ...anthropic, supports_thinking: true });
    expect(write.when_thinking_disabled).toEqual({
      thinking: { type: "disabled" },
    });
  });

  it("writes an EMPTY block — not a deletion — when the endpoint rejects the disable signal", () => {
    const write = buildWrite({
      ...anthropic,
      supports_thinking: true,
      thinking_disabled: "omit",
    });
    // Deleting the key would let the factory re-synthesise the same
    // `thinking: {type: disabled}` payload from `when_thinking_enabled`; `{}`
    // is what stops that chain. Asserting on the key's presence, not just on
    // `toEqual({})`, is the point.
    expect(write).toHaveProperty("when_thinking_disabled");
    expect(write.when_thinking_disabled).toEqual({});
    expect(Object.keys(write.when_thinking_disabled as object)).toHaveLength(0);
    // The enabled block is untouched — the choice only governs the off path.
    expect(write.when_thinking_enabled).toEqual({
      thinking: { type: "enabled", budget_tokens: 4096 },
    });
  });

  it("still clears every thinking key when the box is unticked, even in omit mode", () => {
    const write = buildWrite(
      { ...openai, supports_thinking: false, thinking_disabled: "omit" },
      storedEntry({
        supports_thinking: true,
        when_thinking_enabled: { extra_body: { thinking: { type: "enabled" } } },
        when_thinking_disabled: { extra_body: { thinking: { type: "disabled" } } },
        max_tokens: 8192,
      }),
    );
    // "This model has no thinking at all" outranks the off-path choice.
    expect(write).not.toHaveProperty("supports_thinking");
    expect(write).not.toHaveProperty("when_thinking_enabled");
    expect(write).not.toHaveProperty("when_thinking_disabled");
    expect(write).not.toHaveProperty("max_tokens");
  });

  it("survives an edit round trip: a stored `{}` stays `{}`", () => {
    const stored = storedEntry({
      use: "langchain_anthropic:ChatAnthropic",
      supports_thinking: true,
      when_thinking_enabled: { thinking: { type: "enabled", budget_tokens: 4096 } },
      when_thinking_disabled: {},
    });
    // What the edit form reads back...
    expect(readThinkingDisabledMode(stored)).toBe("omit");
    // ...and what it writes on save, unchanged.
    const write = buildWrite(
      {
        ...anthropic,
        supports_thinking: true,
        thinking_disabled: readThinkingDisabledMode(stored),
      },
      stored,
    );
    expect(write.when_thinking_disabled).toEqual({});
  });
});

describe("the 'what is sent when thinking is off' control", () => {
  /**
   * Open the edit form on a thinking-enabled Anthropic entry, which is where
   * the control lives — it is only meaningful while the checkbox is ticked.
   */
  async function openThinkingForm(stored: Partial<ManagedModel> = {}) {
    modelsState.current = {
      models: [
        managedModel({
          name: "claude-sonnet",
          use: "langchain_anthropic:ChatAnthropic",
          supports_thinking: true,
          when_thinking_enabled: {
            thinking: { type: "enabled", budget_tokens: 4096 },
          },
          ...stored,
        }),
      ],
      isLoading: false,
      error: undefined,
    };
    render(<ModelSettingsPage />);
    (await screen.findByRole("button", { name: "编辑" })).click();
    await screen.findByRole("checkbox");
    return document.getElementById("model-thinking-disabled")!;
  }

  it("offers both options and defaults to the provider's disable block", async () => {
    const trigger = await openThinkingForm();

    expect(trigger).toBeTruthy();
    // The default is "send the preset" — an existing entry must not change
    // meaning just because a new control appeared.
    expect(screen.getByText("发送服务商的禁用参数")).toBeTruthy();
    expect(screen.queryByText(/不传思考参数也会思考/)).toBeNull();
  });

  it("explains the consequence by reusing the 'always on' wording", async () => {
    const trigger = await openThinkingForm();

    fireEvent.keyDown(trigger, { key: "ArrowDown" });
    fireEvent.click(
      screen.getByRole("option", {
        name: "不发送任何禁用参数 —— 该端点不接受禁用信号",
      }),
    );

    // Selecting "send nothing" means the endpoint falls back to its own
    // default — the same thing `thinkAlwaysOn` already says after a probe, so
    // the two never disagree about the same endpoint.
    expect(screen.getByText("该端点不传思考参数也会思考，思考始终开启。")).toBeTruthy();
    expect(screen.queryByText(/多数端点需要它/)).toBeNull();
  });

  it("reads a stored empty block back as 'send nothing'", async () => {
    const trigger = await openThinkingForm({ when_thinking_disabled: {} });

    fireEvent.keyDown(trigger, { key: "ArrowDown" });
    expect(
      screen.getByRole("option", {
        name: "不发送任何禁用参数 —— 该端点不接受禁用信号",
        selected: true,
      }),
    ).toBeTruthy();
  });
});

describe("readThinkingDisabledMode", () => {
  const stored = (value: unknown): ManagedModel => ({
    index: 0,
    name: "claude-sonnet",
    model: "claude-sonnet-5",
    use: "langchain_anthropic:ChatAnthropic",
    when_thinking_disabled: value,
  });

  it("reads an empty block as 'send nothing'", () => {
    expect(readThinkingDisabledMode(stored({}))).toBe("omit");
  });

  it("reads any populated block as 'send the preset'", () => {
    expect(
      readThinkingDisabledMode(stored({ thinking: { type: "disabled" } })),
    ).toBe("preset");
    expect(
      readThinkingDisabledMode(stored({ extra_body: { thinking: {} } })),
    ).toBe("preset");
  });

  it("falls back to 'preset' when nothing is stored", () => {
    // `undefined` and `null` are both "the entry never chose" — never "omit",
    // which would otherwise silently drop a disable signal on the next save.
    expect(readThinkingDisabledMode(stored(undefined))).toBe("preset");
    expect(readThinkingDisabledMode(stored(null))).toBe("preset");
  });

  it("does not mistake an array or a string for the empty block", () => {
    expect(readThinkingDisabledMode(stored([]))).toBe("preset");
    expect(readThinkingDisabledMode(stored(""))).toBe("preset");
  });
});

describe("buildManagedModelProbe", () => {
  function stored(overrides: Partial<ManagedModel> = {}): ManagedModel {
    return {
      index: 0,
      name: "doubao-seed-1.8",
      model: "doubao-seed-1.8",
      use: "langchain_openai:ChatOpenAI",
      ...overrides,
    };
  }

  it("sends a stored $VAR reference so the server resolves it from .env", () => {
    const probe = buildManagedModelProbe(
      stored({ api_key: "$DOUBAO_API_KEY", api_key_masked: false }),
    );
    expect(probe.api_key).toBe("$DOUBAO_API_KEY");
    expect(probe).not.toHaveProperty("api_key_value");
  });

  it("omits the key entirely when the entry stores none", () => {
    expect(buildManagedModelProbe(stored())).not.toHaveProperty("api_key");
  });

  it("carries the entry's provider-specific extras into the probe", () => {
    const probe = buildManagedModelProbe(
      stored({ base_url: "https://example.test/v1" }),
    );
    expect(probe.base_url).toBe("https://example.test/v1");
    expect(probe).not.toHaveProperty("index");
  });
});

describe("provider picker", () => {
  it("uses the selected provider's class path", () => {
    const write = buildWrite({
      ...EMPTY_MODEL_DRAFT,
      name: "doubao-seed-1.8",
      model: "doubao-seed-1-8-251228",
      provider: "doubao",
    });
    expect(write.use).toBe("deerflow.models.patched_deepseek:PatchedChatDeepSeek");
    // A recognized provider must NOT be stored as an override.
    expect(write.use).not.toBe("langchain_openai:ChatOpenAI");
  });

  it("maps a known class path back to its provider on edit", () => {
    expect(
      draftProviderFor(PROVIDERS, "deerflow.models.patched_deepseek:PatchedChatDeepSeek"),
    ).toEqual({ provider: "doubao", useOverride: "" });
  });

  it("does not reverse-map the openai-compatible fallback label", () => {
    // It shares OpenAI's class path, so mapping it back would relabel a
    // perfectly ordinary OpenAI entry as "other OpenAI-compatible".
    expect(draftProviderFor(PROVIDERS, "langchain_openai:ChatOpenAI")).toEqual({
      provider: "openai",
      useOverride: "",
    });
  });

  it("PRESERVES an unrecognised class path verbatim on edit", () => {
    // The critical rule: a hand-written advanced entry must survive an edit.
    // The UI cannot label it, but it must not rewrite it either.
    const exotic = "deerflow.models.mindie_provider:MindIEChatModel";
    const draft = { ...EMPTY_MODEL_DRAFT, name: "mindie", model: "m", ...draftProviderFor(PROVIDERS, exotic) };
    expect(draft.provider).toBe("openai-compatible");

    const write = buildWrite(draft);
    expect(write.use).toBe(exotic);
  });

  it("clears the preserved override once a provider is picked explicitly", () => {
    const exotic = "some.custom:Model";
    const draft = { ...EMPTY_MODEL_DRAFT, name: "x", model: "m", ...draftProviderFor(PROVIDERS, exotic) };
    // Simulate the Select's onChange.
    const picked = { ...draft, provider: "doubao", useOverride: "" };
    expect(buildWrite(picked).use).toBe("deerflow.models.patched_deepseek:PatchedChatDeepSeek");
  });

  it("writes the endpoint under the provider's own field, not a shared api_base", () => {
    // OpenAI 类的字段名是 openai_api_base；共享 api_base 会被静默塞进
    // model_kwargs，直到第一次调用才炸。
    const write = buildWrite({
      ...EMPTY_MODEL_DRAFT, name: "x", model: "m",
      provider: "openai", api_base: "https://example.test/v1",
    });
    expect(write.openai_api_base).toBe("https://example.test/v1");
    expect(write.api_base).toBeUndefined();
  });

  it("uses api_base for the patched providers that declare it", () => {
    const write = buildWrite({
      ...EMPTY_MODEL_DRAFT, name: "x", model: "m",
      provider: "doubao", api_base: "https://ark.test/api/v3",
    });
    expect(write.api_base).toBe("https://ark.test/api/v3");
    expect(write.openai_api_base).toBeUndefined();
  });

  it("drops stale endpoint keys when the provider changes", () => {
    // 从 doubao（api_base）切到 openai（openai_api_base）时，
    // 旧的 api_base 不能残留，否则两个键同时存在。
    const original = {
      index: 0, name: "x", model: "m",
      use: "deerflow.models.patched_deepseek:PatchedChatDeepSeek",
      api_base: "https://ark.test/api/v3",
    };
    const write = buildWrite(
      { ...EMPTY_MODEL_DRAFT, name: "x", model: "m", provider: "openai", api_base: "https://oai.test/v1" },
      original,
    );
    expect(write.openai_api_base).toBe("https://oai.test/v1");
    expect(write.api_base).toBeUndefined();
  });

  it("still writes the endpoint key when the field is blank, so clearing removes it", () => {
    const write = buildWrite({
      ...EMPTY_MODEL_DRAFT, name: "x", model: "m", provider: "openai", api_base: "",
    });
    expect(write.openai_api_base).toBe("");
  });
});
