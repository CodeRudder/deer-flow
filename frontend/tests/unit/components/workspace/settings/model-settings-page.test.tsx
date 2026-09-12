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
import { cleanup, render, screen } from "@testing-library/react";

import {
  buildManagedModelProbe,
  buildManagedModelWrite,
  deriveApiKeyVarName,
  EMPTY_MODEL_DRAFT,
  isManagedModelDraftComplete,
  maskedKeyLabel,
  ModelSettingsPage,
} from "@/components/workspace/settings/model-settings-page";
import { ModelConfigRequestError } from "@/core/models/api";
import type { ManagedModel } from "@/core/models/types";

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

rs.mock("@/core/models/hooks", () => ({
  useManagedModels: () => modelsState.current,
  useSaveManagedModel: () => ({ mutate: rs.fn(), isPending: false }),
  useDeleteManagedModel: () => ({ mutate: rs.fn(), isPending: false }),
  useTestManagedModel: () => ({ mutate: rs.fn(), isPending: false }),
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
        },
      },
    },
  }),
}));

afterEach(() => {
  cleanup();
  modelsState.current = { models: [], isLoading: false, error: undefined };
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

describe("isManagedModelDraftComplete", () => {
  it("requires name, use and model", () => {
    expect(
      isManagedModelDraftComplete({
        ...EMPTY_MODEL_DRAFT,
        name: "gpt-4o",
        use: "langchain_openai:ChatOpenAI",
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
        use: " langchain_openai:ChatOpenAI ",
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
    use: "langchain_openai:ChatOpenAI",
    model: "gpt-4o",
  };

  it("trims the required fields", () => {
    const write = buildManagedModelWrite({
      ...complete,
      name: " gpt-4o ",
      model: " gpt-4o ",
    });
    expect(write.name).toBe("gpt-4o");
    expect(write.model).toBe("gpt-4o");
  });

  it("omits both key fields when the user did not retype a key", () => {
    const write = buildManagedModelWrite(complete);
    expect(write).not.toHaveProperty("api_key");
    expect(write).not.toHaveProperty("api_key_value");
  });

  it("does not prefill a key when editing an entry with a stored secret", () => {
    const write = buildManagedModelWrite(complete, {
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
    const write = buildManagedModelWrite({
      ...complete,
      name: "doubao-seed-1.8",
      api_key: "sk-live-1234",
    });
    expect(write.api_key).toBe("$DOUBAO_SEED_1_8_API_KEY");
    expect(write.api_key_value).toBe("sk-live-1234");
    expect(write.api_key).not.toContain("sk-live-1234");
  });

  it("derives the name from the edited entry's name", () => {
    const write = buildManagedModelWrite(
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
    const write = buildManagedModelWrite({
      ...complete,
      api_key: "$BRAND_NEW_KEY",
    });
    expect(write.api_key).toBe("$BRAND_NEW_KEY");
    expect(write).not.toHaveProperty("api_key_value");
  });

  it("keeps provider-specific extras an edit does not expose", () => {
    const write = buildManagedModelWrite(complete, {
      index: 2,
      name: "gpt-4o",
      model: "gpt-4o",
      use: "langchain_openai:ChatOpenAI",
      base_url: "https://example.test/v1",
      max_tokens: 4096,
      api_key_masked: true,
    });
    expect(write.base_url).toBe("https://example.test/v1");
    expect(write.max_tokens).toBe(4096);
    // Server-owned addressing metadata never round-trips into the file.
    expect(write).not.toHaveProperty("index");
    expect(write).not.toHaveProperty("api_key_masked");
  });

  it("normalizes a blank display_name to null", () => {
    expect(buildManagedModelWrite(complete).display_name).toBeNull();
    expect(
      buildManagedModelWrite({ ...complete, display_name: " GPT-4o " })
        .display_name,
    ).toBe("GPT-4o");
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
