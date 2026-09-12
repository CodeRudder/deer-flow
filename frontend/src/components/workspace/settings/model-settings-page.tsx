"use client";

import {
  CheckIcon,
  Loader2Icon,
  PencilIcon,
  PlusIcon,
  Trash2Icon,
  XIcon,
} from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemTitle,
} from "@/components/ui/item";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useI18n } from "@/core/i18n/hooks";
import { ModelConfigRequestError } from "@/core/models/api";
import {
  useDeleteManagedModel,
  useManagedModels,
  useModelProviders,
  useProbeManagedModelThinking,
  useSaveManagedModel,
  useTestManagedModel,
} from "@/core/models/hooks";
import type {
  ManagedModel,
  ManagedModelWrite,
  ModelProvider,
  ModelTestResult,
  ThinkingProbeResult,
} from "@/core/models/types";
import { cn } from "@/lib/utils";

import { SettingsSection } from "./settings-section";

/**
 * Radix Select forbids an empty item value, so "no provider picked yet" and the
 * fallback for an unrecognised class path share one sentinel.
 */
export const PROVIDER_FALLBACK_KEY = "openai-compatible";

/** Form state for the add/edit panel. Every field is a string; blanks are unset. */
export interface ManagedModelDraft {
  name: string;
  model: string;
  /**
   * The selected provider *key*, not a class path. `use` is derived from it on
   * save (or, for an unrecognised stored entry, preserved verbatim by the
   * `useOverride` below) so the operator never sees a class path.
   */
  provider: string;
  /**
   * The stored `use` of an entry the preset table does not recognise.
   *
   * Load-bearing: a hand-crafted entry using a class path outside the presets
   * must survive an edit byte-for-byte. When set, it wins over the provider's
   * own `use` on save; when blank, the provider's `use` is used.
   */
  useOverride: string;
  api_base: string;
  display_name: string;
  api_key: string;
  /** Whether the operator ticked "this model supports thinking". */
  supports_thinking: boolean;
  /**
   * Thinking budget, as text. Only meaningful when the provider's API requires
   * one (Anthropic, Google) — other providers reject it and it is not written.
   */
  budget_tokens: string;
  /** Output ceiling, as text. Written whenever non-blank. */
  max_tokens: string;
}

export const EMPTY_MODEL_DRAFT: ManagedModelDraft = {
  name: "",
  model: "",
  provider: "",
  useOverride: "",
  api_base: "",
  display_name: "",
  api_key: "",
  supports_thinking: false,
  // Prefilled from the provider preset on selection; Anthropic requires an
  // explicit budget and pairs it with a larger max_tokens (budget must be less).
  budget_tokens: "",
  max_tokens: "",
};

/**
 * The provider preset matching a stored class path, or `null`.
 *
 * The `openai-compatible` row is skipped deliberately: it shares
 * `langchain_openai:ChatOpenAI` with `openai` but is a *fallback label*, not the
 * reverse mapping — otherwise an advanced entry would silently relabel itself.
 */
export function findProviderByUse(
  providers: ModelProvider[],
  use: string,
): ModelProvider | null {
  return (
    providers.find(
      (provider) =>
        provider.key !== PROVIDER_FALLBACK_KEY && provider.use === use,
    ) ?? null
  );
}

/**
 * The provider-draft state for an existing entry.
 *
 * A recognised class path reverse-maps to its provider. An unrecognised one
 * falls back to the `openai-compatible` label for display but records the
 * original `use` in `useOverride`, so saving keeps exactly what was stored.
 */
export function draftProviderFor(
  providers: ModelProvider[],
  use: string,
): Pick<ManagedModelDraft, "provider" | "useOverride"> {
  const match = findProviderByUse(providers, use);
  if (match) {
    return { provider: match.key, useOverride: "" };
  }
  return { provider: PROVIDER_FALLBACK_KEY, useOverride: use };
}

/** Whether the selected provider requires an explicit base URL. */
function providerRequiresApiBase(provider: string): boolean {
  return provider === PROVIDER_FALLBACK_KEY;
}

/**
 * Overlay the operator's budget onto a provider's thinking template.
 *
 * The budget lives at a different path for each family — Anthropic nests it as
 * `thinking.budget_tokens`, Google puts it at the top level as
 * `thinking_budget` — so this walks the known shapes rather than assuming one.
 * Providers that take no budget (`needsBudget` false, e.g. OpenAI-compatible
 * `extra_body.thinking`) get the template unchanged: sending them a budget would
 * be an unrecognised key, which `ModelConfig`'s `extra="allow"` would pass
 * straight through to the SDK.
 *
 * A blank or non-numeric budget also leaves the template alone, keeping the
 * preset's own default (4096).
 */
export function readBudget(
  template: Record<string, unknown> | null,
): string {
  if (!template) {
    return "";
  }
  const nested = template.thinking;
  if (nested && typeof nested === "object") {
    const value = (nested as Record<string, unknown>).budget_tokens;
    if (typeof value === "number") {
      return String(value);
    }
  }
  const flat = template.thinking_budget;
  return typeof flat === "number" ? String(flat) : "";
}

export function withBudget(
  template: Record<string, unknown> | null,
  budget: string,
  needsBudget: boolean,
): Record<string, unknown> | null {
  if (!template || !needsBudget) {
    return template;
  }
  const parsed = Number(budget.trim());
  if (!budget.trim() || !Number.isFinite(parsed) || parsed <= 0) {
    return template;
  }

  const next = structuredClone(template);
  const nested = next.thinking;
  if (nested && typeof nested === "object" && "budget_tokens" in nested) {
    (nested as Record<string, unknown>).budget_tokens = parsed;
    return next;
  }
  if ("thinking_budget" in next) {
    next.thinking_budget = parsed;
    return next;
  }
  return next;
}

/**
 * `name`/`model`/`provider` are required, plus a base URL for the
 * OpenAI-compatible provider (which has no default endpoint to fall back on).
 * The rest are optional.
 */
export function isManagedModelDraftComplete(draft: ManagedModelDraft): boolean {
  return (
    [draft.name, draft.model, draft.provider].every(
      (value) => value.trim().length > 0,
    ) &&
    (!providerRequiresApiBase(draft.provider) ||
      draft.api_base.trim().length > 0)
  );
}

/**
 * The key to show for an entry: the masked literal when the entry stores one, a
 * `$VAR` reference verbatim (the UI needs it to show which variable the entry
 * points at), else nothing.
 */
export function maskedKeyLabel(model: ManagedModel): string | null {
  const masked = model.api_key_masked;
  if (typeof masked === "string" && masked.length > 0) {
    return masked;
  }
  const key = model.api_key;
  if (typeof key === "string" && key.length > 0) {
    return key;
  }
  return null;
}

/**
 * Every endpoint key a provider class may accept.
 *
 * Not shared: OpenAI takes `openai_api_base`, Anthropic `anthropic_api_url`,
 * Google `base_url`, the patched DeepSeek/MiniMax family `api_base`. The form
 * writes exactly one of these (the one the selected provider declares) and must
 * drop the others, or a provider switch would leave a stale endpoint behind.
 */
export const BASE_URL_KEYS = [
  "api_base",
  "base_url",
  "openai_api_base",
  "anthropic_api_url",
] as const;

//: Server-owned / write-only fields that must never be written back verbatim.
const NON_ROUND_TRIP_KEYS = new Set<string>([
  "index",
  "api_key_masked",
  "api_key",
  "api_key_value",
  // The form owns the endpoint; it is rebuilt from the draft on save so a
  // cleared field actually removes the stored value instead of being carried
  // over by `carriedOver`.
  ...BASE_URL_KEYS,
]);

/**
 * Provider-specific extras (and `description`) that the form does not expose.
 *
 * `PUT /api/models/{name}` replaces the whole entry from the posted payload, so
 * an edit that only carried the form fields would silently drop
 * `when_thinking_enabled`, `base_url`, `max_tokens`, ... — carry them over,
 * minus the fields the server owns or the form values.
 */
function carriedOver(original?: ManagedModel): Record<string, unknown> {
  if (!original) {
    return {};
  }
  return Object.fromEntries(
    Object.entries(original).filter(([key]) => !NON_ROUND_TRIP_KEYS.has(key)),
  );
}

/**
 * Derive the `.env` variable name a typed cleartext gets stored under.
 *
 * Uppercased, every non-alphanumeric run collapsed to a single `_`, and
 * suffixed `_API_KEY` — `doubao-seed-1.8` → `DOUBAO_SEED_1_8_API_KEY`. A leading
 * digit would make the name unusable in a shell, so it is prefixed with `_`, and
 * a name with nothing usable left (`模型`) falls back to `MODEL`.
 *
 * Names that differ only in punctuation collapse to the same variable — which
 * is why the UI shows the operator the derived name.
 */
export function deriveApiKeyVarName(name: string): string {
  const normalized = name
    .trim()
    .toUpperCase()
    .replace(/[^A-Z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
  const base = normalized || "MODEL";
  return `${/^[0-9]/.test(base) ? `_${base}` : base}_API_KEY`;
}

/**
 * Build the wire payload for a save.
 *
 * One key field, three cases — the operator types a credential, never a storage
 * strategy:
 *
 * - blank → both key fields are omitted, so the backend keeps the stored secret;
 * - `$VAR` → passed through as an environment reference;
 * - anything else → a literal, stored in `.env` under a name derived from the
 *   entry and referenced from `config.yaml`. Keeping cleartexts out of the
 *   config file is the convention the deployment scripts already rely on.
 */
export function buildManagedModelWrite(
  draft: ManagedModelDraft,
  providers: ModelProvider[],
  original?: ManagedModel,
): ManagedModelWrite {
  // An unrecognised class path (a hand-written entry) is carried in
  // `useOverride` and wins; otherwise the class path comes from the selected
  // provider preset. This is what lets an advanced entry survive an edit
  // instead of being silently rewritten to the fallback provider's class path.
  const override = draft.useOverride.trim();
  const presetUse = providers.find((p) => p.key === draft.provider)?.use ?? "";
  const use = override !== "" ? override : presetUse;

  const write: ManagedModelWrite = {
    ...carriedOver(original),
    name: draft.name.trim(),
    use,
    model: draft.model.trim(),
    display_name: draft.display_name.trim() || null,
  };

  // The endpoint goes under the key THIS provider accepts — it is not shared
  // (`openai_api_base` / `anthropic_api_url` / `base_url` / `api_base`).
  // Writing a key the provider does not take is silent: ModelConfig is
  // `extra="allow"`, so the value is forwarded into the SDK's model_kwargs and
  // only fails on the first real request. The backend rejects a mismatched key
  // at save time, but carrying the override's class path means the provider
  // lookup above may miss — in that case fall back to `api_base`, which is
  // what a hand-written entry most likely already uses.
  const provider = providers.find((p) => p.key === draft.provider);
  const baseUrlKey = provider?.api_base_field ?? "api_base";
  // Always write the key, even when blank, so a cleared field removes the
  // stored endpoint instead of letting `carriedOver` restore it.
  write[baseUrlKey] = draft.api_base.trim();
  // Drop any endpoint key left over from a previous provider selection (or
  // carried over from the original entry), so only the chosen one remains.
  for (const key of BASE_URL_KEYS) {
    if (key !== baseUrlKey) delete write[key];
  }

  // Thinking. The checkbox only decides when the provider can actually express
  // thinking; otherwise the stored keys are carried over untouched.
  //
  // - checked → write the trio from the preset. The block shapes are never
  //   synthesised here — they differ per provider (`thinking` /
  //   `extra_body.thinking` / `thinking_budget` / `chat_template_kwargs`).
  // - unchecked after having been on (`supports_thinking: true` stored) → drop
  //   every thinking key. This is the only route the operator has to turn
  //   thinking off, and `supports_thinking` is the flag DeerFlow actually
  //   consults, so leaving it behind would keep thinking on while the UI said
  //   otherwise.
  // - otherwise (never enabled, or the provider has no thinking template) →
  //   leave whatever was stored. A hand-written `when_thinking_enabled` the
  //   form cannot express survives an edit instead of being silently rewritten
  //   to the preset or dropped.
  if (provider?.supports_thinking) {
    if (draft.supports_thinking) {
      write.supports_thinking = true;
      write.when_thinking_enabled = withBudget(
        provider.thinking_enabled,
        draft.budget_tokens,
        provider.thinking_needs_budget,
      );
      // `thinking` is the legacy top-level alias; the preset supersedes it.
      delete write.thinking;
      if (provider.thinking_disabled) {
        write.when_thinking_disabled = provider.thinking_disabled;
      } else {
        delete write.when_thinking_disabled;
      }

      // `max_tokens` is the thinking block's companion field: it has to clear
      // the budget, and the form only shows it alongside the checkbox. Blank
      // means "no explicit ceiling", so the stored value is removed rather than
      // carried over.
      delete write.max_tokens;
      const maxTokens = draft.max_tokens.trim();
      if (maxTokens) {
        write.max_tokens = Number(maxTokens);
      }
    } else if (original?.supports_thinking === true) {
      delete write.supports_thinking;
      delete write.when_thinking_enabled;
      delete write.when_thinking_disabled;
      delete write.thinking;
      delete write.max_tokens;
    }
  }

  const apiKey = draft.api_key.trim();
  if (apiKey) {
    if (apiKey.startsWith("$")) {
      write.api_key = apiKey;
    } else {
      write.api_key = `$${deriveApiKeyVarName(write.name)}`;
      write.api_key_value = apiKey;
    }
  }
  return write;
}

/**
 * Build the probe payload for a stored entry.
 *
 * The probe endpoint resolves a `$VAR` reference from the process environment
 * server-side, so the stored reference is exactly what it needs. A stored
 * literal only ever reaches the browser as a mask, so an entry whose key is a
 * literal can only be probed after the operator retypes it into the form.
 */
export function buildManagedModelProbe(model: ManagedModel): ManagedModelWrite {
  const probe: ManagedModelWrite = {
    ...carriedOver(model),
    name: model.name,
    use: model.use,
    model: model.model,
    display_name: model.display_name ?? null,
  };
  // The probe must exercise the entry AS STORED — including its endpoint.
  // `carriedOver` strips every BASE_URL_KEYS entry (the form owns that field on
  // save), so re-attach whichever key this entry actually uses; otherwise the
  // probe would test against the SDK default and could report a false failure
  // for a custom endpoint.
  for (const baseUrlKey of BASE_URL_KEYS) {
    const value = model[baseUrlKey];
    if (typeof value === "string" && value.length > 0) {
      probe[baseUrlKey] = value;
    }
  }
  const key = model.api_key;
  if (typeof key === "string" && key.length > 0) {
    probe.api_key = key;
  }
  return probe;
}

export function ModelSettingsPage() {
  const { t } = useI18n();
  const { models, isLoading, error } = useManagedModels();
  // Providers are only fetched for an admin; the form needs them to render the
  // dropdown, and the reverse mapping needs them to label an existing entry.
  const { providers } = useModelProviders();
  const adminRequired =
    error instanceof ModelConfigRequestError && error.isAdminRequired;

  return (
    <SettingsSection
      title={t.settings.models.title}
      description={t.settings.models.description}
    >
      {isLoading ? (
        <div className="text-muted-foreground text-sm">{t.common.loading}</div>
      ) : adminRequired ? (
        <div className="text-muted-foreground text-sm">
          {t.settings.models.adminRequired}
        </div>
      ) : error ? (
        <div className="text-sm">
          {t.settings.models.loadError.replace("{detail}", error.message)}
        </div>
      ) : (
        <ManagedModelList models={models} providers={providers} />
      )}
    </SettingsSection>
  );
}

function ManagedModelList({
  models,
  providers,
}: {
  models: ManagedModel[];
  providers: ModelProvider[];
}) {
  const { t } = useI18n();
  const { mutate: saveModel, isPending: isSaving } = useSaveManagedModel();
  const { mutate: deleteModel, isPending: isDeleting } =
    useDeleteManagedModel();
  const { mutate: testModel, isPending: isTesting } = useTestManagedModel();
  const { mutate: probeThinking, isPending: isProbing } =
    useProbeManagedModelThinking();

  const [draft, setDraft] = useState<ManagedModelDraft | null>(null);
  const [editing, setEditing] = useState<ManagedModel | null>(null);
  const [pendingDelete, setPendingDelete] = useState<ManagedModel | null>(null);
  const [testResults, setTestResults] = useState<
    Record<string, ModelTestResult>
  >({});
  const [testingName, setTestingName] = useState<string | null>(null);
  // A probe verdict is tied to the draft it was produced from; any edit clears it
  // so a stale "supports thinking" can never be read as a fresh one.
  const [thinkingProbe, setThinkingProbe] = useState<ThinkingProbeResult | null>(
    null,
  );

  const updateDraft = (next: ManagedModelDraft | null) => {
    setThinkingProbe(null);
    setDraft(next);
  };

  const runThinkingProbe = () => {
    if (!draft) {
      return;
    }
    // An edit posts no credential — the form only ever shows the stored `$VAR`
    // reference (or a mask), never the secret. Pass the reference along under a
    // private key so the server can resolve it from `.env` the same way a save
    // would; without it the probe authenticates with nothing and reports "could
    // not resolve authentication method" for a perfectly good model.
    const stored = editing?.api_key;
    const probeEntry = buildManagedModelWrite(
      draft,
      providers,
      editing ?? undefined,
    );
    if (!draft.api_key.trim() && typeof stored === "string") {
      probeEntry._stored_api_key = stored;
    }
    probeThinking(probeEntry, {
      onSuccess: (result) => setThinkingProbe(result),
      onError: (probeError) =>
        setThinkingProbe({
          ok: false,
          thinks_by_default: false,
          respects_enabled: false,
          respects_disabled: false,
          latency_ms: 0,
          error:
            probeError instanceof Error
              ? probeError.message
              : String(probeError),
        }),
    });
  };

  const openCreate = () => {
    // A verdict from a previous form must not carry over: it describes a
    // different entry, and the UI presents it as fact about this one.
    setThinkingProbe(null);
    setEditing(null);
    setDraft({ ...EMPTY_MODEL_DRAFT });
  };

  const openEdit = (model: ManagedModel) => {
    setThinkingProbe(null);
    setEditing(model);
    // The stored server may hold the endpoint under any of the provider keys
    // (or none), so read whichever one is present rather than assuming.
    const storedBase = BASE_URL_KEYS.map((key) => model[key]).find(
      (value): value is string => typeof value === "string" && value.length > 0,
    );
    const apiBase = storedBase ?? "";
    // Thinking state is read back from whatever the entry actually stores —
    // `supports_thinking` is the flag DeerFlow consults, so it is the source of
    // truth. The budget is dug out of whichever shape this entry uses.
    const storedThinking = (model.when_thinking_enabled ?? null) as Record<
      string,
      unknown
    > | null;
    setDraft({
      ...EMPTY_MODEL_DRAFT,
      name: model.name,
      // Reverse-maps the stored class path to a provider label; an unrecognised
      // path lands in `useOverride` so saving keeps it verbatim.
      ...draftProviderFor(providers, model.use),
      model: model.model,
      display_name: model.display_name ?? "",
      api_base: apiBase,
      supports_thinking: model.supports_thinking === true,
      budget_tokens: readBudget(storedThinking),
      max_tokens:
        typeof model.max_tokens === "number" ? String(model.max_tokens) : "",
    });
  };

  const closeForm = () => {
    setDraft(null);
    setEditing(null);
  };

  const handleSave = () => {
    if (!draft) {
      return;
    }
    if (!isManagedModelDraftComplete(draft)) {
      toast.error(t.settings.models.required);
      return;
    }
    // `editing` addresses the stored entry; `draft.name` may have been changed.
    const payload = buildManagedModelWrite(draft, providers, editing ?? undefined);
    saveModel(
      { name: editing?.name, model: payload },
      {
        onSuccess: () => {
          toast.success(t.settings.models.saveSuccess);
          closeForm();
        },
        onError: (saveError) => {
          toast.error(
            saveError instanceof Error ? saveError.message : String(saveError),
          );
        },
      },
    );
  };

  const handleDelete = (model: ManagedModel) => {
    setPendingDelete(null);
    deleteModel(model.name, {
      onSuccess: () => {
        toast.success(t.settings.models.deleteSuccess);
      },
      onError: (deleteError) => {
        toast.error(
          deleteError instanceof Error
            ? deleteError.message
            : String(deleteError),
        );
      },
    });
  };

  const handleTest = (model: ManagedModel) => {
    setTestingName(model.name);
    // The stored entry is the candidate — a `$VAR` reference is resolved from the
    // server's environment, which is the only way the stored secret can reach a
    // probe (literals only ever arrive here masked).
    testModel(buildManagedModelProbe(model), {
      onSuccess: (result) => {
        setTestResults((current) => ({ ...current, [model.name]: result }));
      },
      onError: (testError) => {
        setTestResults((current) => ({
          ...current,
          [model.name]: {
            ok: false,
            latency_ms: 0,
            error:
              testError instanceof Error
                ? testError.message
                : String(testError),
          },
        }));
      },
      onSettled: () => {
        setTestingName(null);
      },
    });
  };

  return (
    <div className="flex w-full flex-col gap-4">
      <div className="flex justify-end">
        <Button
          disabled={draft !== null}
          onClick={openCreate}
          size="sm"
          type="button"
        >
          <PlusIcon />
          {t.settings.models.add}
        </Button>
      </div>

      {draft && (
        <ModelFormPanel
          draft={draft}
          isSaving={isSaving}
          maskedKey={editing ? maskedKeyLabel(editing) : null}
          onChange={updateDraft}
          onClose={closeForm}
          onProbe={runThinkingProbe}
          onSave={handleSave}
          probe={thinkingProbe}
          probing={isProbing}
          providers={providers}
          title={
            editing ? t.settings.models.editTitle : t.settings.models.addTitle
          }
        />
      )}

      {models.length === 0 ? (
        <div className="text-muted-foreground text-sm">
          {t.settings.models.empty}
        </div>
      ) : (
        models.map((model) => {
          const result = testResults[model.name];
          const maskedKey = maskedKeyLabel(model);
          return (
            <Item className="w-full" key={model.name} variant="outline">
              <ItemContent>
                <ItemTitle>
                  <div className="flex flex-wrap items-center gap-2">
                    <span>{model.display_name ?? model.name}</span>
                    <span className="text-muted-foreground font-mono text-xs">
                      {model.name}
                    </span>
                  </div>
                </ItemTitle>
                <ItemDescription className="font-mono text-xs">
                  {model.use}
                </ItemDescription>
                <ItemDescription className="text-xs">
                  {t.settings.models.model}: {model.model}
                  {" · "}
                  {t.settings.models.apiKey}:{" "}
                  {maskedKey ?? t.settings.models.keyNotSet}
                </ItemDescription>
                {result && (
                  <div
                    className={
                      result.ok
                        ? "text-sm text-emerald-600"
                        : "text-destructive text-sm"
                    }
                  >
                    {result.ok ? (
                      <span className="inline-flex items-center gap-1">
                        <CheckIcon className="size-4" />
                        {t.settings.models.testOk.replace(
                          "{latency}",
                          String(result.latency_ms),
                        )}
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1">
                        <XIcon className="size-4" />
                        {t.settings.models.testFailed.replace(
                          "{detail}",
                          result.error ?? "",
                        )}
                      </span>
                    )}
                  </div>
                )}
              </ItemContent>
              <ItemActions>
                <Button
                  disabled={isTesting && testingName === model.name}
                  onClick={() => handleTest(model)}
                  size="sm"
                  type="button"
                  variant="outline"
                >
                  {isTesting && testingName === model.name && (
                    <Loader2Icon className="animate-spin" />
                  )}
                  {t.settings.models.test}
                </Button>
                <Button
                  aria-label={t.settings.models.edit}
                  disabled={draft !== null}
                  onClick={() => openEdit(model)}
                  size="icon-sm"
                  type="button"
                  variant="ghost"
                >
                  <PencilIcon />
                </Button>
                <Button
                  aria-label={t.settings.models.delete}
                  className="text-destructive hover:text-destructive"
                  disabled={isDeleting}
                  onClick={() => setPendingDelete(model)}
                  size="icon-sm"
                  type="button"
                  variant="ghost"
                >
                  <Trash2Icon />
                </Button>
              </ItemActions>
            </Item>
          );
        })
      )}

      <AlertDialog
        open={pendingDelete !== null}
        onOpenChange={(open) => {
          if (!open) {
            setPendingDelete(null);
          }
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t.settings.models.deleteConfirmTitle}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {t.settings.models.deleteConfirm.replace(
                "{name}",
                pendingDelete?.name ?? "",
              )}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t.settings.models.cancel}</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive hover:bg-destructive/90 text-white"
              onClick={() => {
                if (pendingDelete) {
                  handleDelete(pendingDelete);
                }
              }}
            >
              {t.settings.models.delete}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function ModelFormPanel({
  draft,
  maskedKey,
  providers,
  probe,
  probing,
  title,
  isSaving,
  onChange,
  onClose,
  onProbe,
  onSave,
}: {
  draft: ManagedModelDraft;
  maskedKey: string | null;
  providers: ModelProvider[];
  probe: ThinkingProbeResult | null;
  probing: boolean;
  title: string;
  isSaving: boolean;
  onChange: (draft: ManagedModelDraft) => void;
  onClose: () => void;
  onProbe: () => void;
  onSave: () => void;
}) {
  const { t } = useI18n();
  const complete = isManagedModelDraftComplete(draft);
  const provider = providers.find((p) => p.key === draft.provider) ?? null;
  const typedKey = draft.api_key.trim();

  // A typed literal is stored in `.env` under a derived name — say which, so the
  // operator can find their key. A `$VAR` is already named by the operator, and
  // an untouched field on an existing entry keeps the stored secret.
  const apiKeyHint = typedKey
    ? typedKey.startsWith("$")
      ? undefined
      : t.settings.models.apiKeyStoredAsHint.replace(
          "{name}",
          deriveApiKeyVarName(draft.name),
        )
    : maskedKey
      ? t.settings.models.apiKeyKeepHint
      : undefined;

  // Restricted to the string-valued keys, so the value/onChange pair always
  // satisfies an <Input>. The boolean `supports_thinking` is driven by the
  // checkbox in ThinkingFields instead.
  type TextFieldKey = {
    [K in keyof ManagedModelDraft]: ManagedModelDraft[K] extends string
      ? K
      : never;
  }[keyof ManagedModelDraft];

  const field = (key: TextFieldKey) => ({
    value: draft[key],
    onChange: (event: React.ChangeEvent<HTMLInputElement>) =>
      onChange({ ...draft, [key]: event.target.value }),
  });

  return (
    <div className="bg-muted/40 space-y-4 rounded-lg border p-4">
      <div className="text-sm font-semibold">{title}</div>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label={t.settings.models.name}>
          <Input
            autoComplete="off"
            placeholder={t.settings.models.namePlaceholder}
            {...field("name")}
          />
        </Field>
        <Field label={t.settings.models.displayName}>
          <Input
            autoComplete="off"
            placeholder={t.settings.models.displayNamePlaceholder}
            {...field("display_name")}
          />
        </Field>
        <Field label={t.settings.models.provider}>
          <Select
            value={draft.provider}
            onValueChange={(provider) => {
              const preset = providers.find((p) => p.key === provider);
              onChange({
                ...draft,
                provider,
                // Picking a provider clears any preserved class path — the
                // operator has explicitly chosen a different one.
                useOverride: "",
                // Prefill the endpoint only when the preset has one and the
                // operator has not typed their own.
                api_base:
                  preset?.default_api_base ?? draft.api_base,
              });
            }}
          >
            <SelectTrigger id="model-provider" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {providers.map((provider) => (
                <SelectItem
                  key={provider.key}
                  value={provider.key}
                  disabled={!provider.available}
                >
                  {provider.label}
                  {!provider.available && (
                    // The package is not installed, so the save path would
                    // reject this provider. Say why rather than offering a
                    // dead end.
                    <span className="text-muted-foreground ml-2 text-xs">
                      {t.settings.models.providerUnavailable}
                    </span>
                  )}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>
        <Field label={t.settings.models.model}>
          <Input
            autoComplete="off"
            className="font-mono"
            placeholder={t.settings.models.modelPlaceholder}
            {...field("model")}
          />
        </Field>
        <Field
          className="sm:col-span-2"
          hint={
            providerRequiresApiBase(draft.provider)
              ? t.settings.models.apiBaseRequiredHint
              : t.settings.models.apiBaseOptionalHint
          }
          label={t.settings.models.apiBase}
        >
          <Input
            autoComplete="off"
            className="font-mono"
            placeholder={t.settings.models.apiBasePlaceholder}
            {...field("api_base")}
          />
        </Field>
        <Field
          className="sm:col-span-2"
          hint={apiKeyHint}
          label={t.settings.models.apiKey}
        >
          <Input
            autoComplete="off"
            className="font-mono"
            placeholder={maskedKey ?? t.settings.models.apiKeyPlaceholder}
            {...field("api_key")}
          />
        </Field>

        <ThinkingFields
          canProbe={complete}
          draft={draft}
          onChange={onChange}
          onProbe={onProbe}
          probe={probe}
          probing={probing}
          provider={provider}
        />
      </div>
      <div className="flex items-center justify-end gap-2">
        <Button onClick={onClose} size="sm" type="button" variant="ghost">
          {t.settings.models.cancel}
        </Button>
        <Button
          disabled={isSaving || !complete}
          onClick={onSave}
          size="sm"
          type="button"
        >
          {isSaving ? t.settings.models.saving : t.settings.models.save}
        </Button>
      </div>
    </div>
  );
}

/**
 * The thinking block: a checkbox, an optional budget, and the probe button.
 *
 * The probe is manual (a real LLM call costs tokens and seconds), so the result
 * is held in the parent and cleared whenever the form changes — a stale verdict
 * would be worse than none.
 */
function ThinkingFields({
  canProbe,
  draft,
  provider,
  probe,
  probing,
  onChange,
  onProbe,
}: {
  /** The draft has everything the probe call needs (name, provider, model). */
  canProbe: boolean;
  draft: ManagedModelDraft;
  provider: ModelProvider | null;
  probe: ThinkingProbeResult | null;
  probing: boolean;
  onChange: (draft: ManagedModelDraft) => void;
  onProbe: () => void;
}) {
  const { t } = useI18n();
  const available = Boolean(provider?.supports_thinking);

  return (
    <div className="sm:col-span-2 rounded-lg border bg-card p-3">
      <div className="flex items-center justify-between gap-3">
        <label className="flex items-center gap-2 text-sm font-medium">
          <input
            checked={draft.supports_thinking}
            className="size-4 accent-primary"
            disabled={!available}
            onChange={(event) =>
              onChange({ ...draft, supports_thinking: event.target.checked })
            }
            type="checkbox"
          />
          {t.settings.models.supportsThinking}
        </label>
        <Button
          disabled={probing || !available || !canProbe}
          onClick={onProbe}
          size="sm"
          type="button"
          variant="outline"
        >
          {probing && <Loader2Icon className="size-3.5 animate-spin" />}
          {t.settings.models.probeThinking}
        </Button>
      </div>

      {available && (
        <div className="text-muted-foreground mt-2 text-xs">
          {t.settings.models.supportsThinkingHint}
        </div>
      )}

      {!available && (
        <div className="text-muted-foreground mt-2 text-xs">
          {t.settings.models.thinkingUnavailable}
        </div>
      )}

      {draft.supports_thinking && available && (
        <div className="mt-3 grid gap-4 sm:grid-cols-2">
          {provider?.thinking_needs_budget && (
            <Field
              hint={t.settings.models.budgetHint}
              label={t.settings.models.budgetTokens}
            >
              <Input
                autoComplete="off"
                className="font-mono"
                inputMode="numeric"
                onChange={(event) =>
                  onChange({ ...draft, budget_tokens: event.target.value })
                }
                placeholder="4096"
                value={draft.budget_tokens}
              />
            </Field>
          )}
          <Field
            hint={t.settings.models.maxTokensHint}
            label={t.settings.models.maxTokens}
          >
            <Input
              autoComplete="off"
              className="font-mono"
              inputMode="numeric"
              onChange={(event) =>
                onChange({ ...draft, max_tokens: event.target.value })
              }
              placeholder="8192"
              value={draft.max_tokens}
            />
          </Field>
        </div>
      )}

      <ThinkingProbeReport probe={probe} probing={probing} />
    </div>
  );
}

/** Renders the probe's three independent observations. */
function ThinkingProbeReport({
  probe,
  probing,
}: {
  probe: ThinkingProbeResult | null;
  /** A probe is in flight — the result below is stale, so don't show it. */
  probing: boolean;
}) {
  const { t } = useI18n();
  // A probe sends 3-5 real requests and takes 20-30s. Without this the panel
  // kept showing "Not detected yet" for the whole wait, which reads as "the
  // button did nothing" — the verdict then appeared all at once, seemingly
  // unresponsive. Say what is happening and how long it takes.
  if (probing) {
    return (
      <div className="text-muted-foreground mt-3 flex items-start gap-2 text-xs">
        <Loader2Icon className="mt-px size-3.5 shrink-0 animate-spin" />
        <span>{t.settings.models.thinkProbing}</span>
      </div>
    );
  }
  if (!probe) {
    return (
      <div className="text-muted-foreground mt-3 text-xs">
        {t.settings.models.thinkNotProbed}
      </div>
    );
  }
  if (!probe.ok) {
    return (
      <div className="text-destructive mt-3 text-xs">
        {probe.error
          ? `${t.settings.models.thinkProbeFailed} ${probe.error}`
          : t.settings.models.thinkProbeFailed}
      </div>
    );
  }

  return (
    <div className="mt-3 space-y-1.5 text-xs">
      <ThinkFinding
        ok={probe.respects_enabled}
        text={probe.respects_enabled ? t.settings.models.thinkEnabled : t.settings.models.thinkNotEnabled}
      />
      {/* "Can it be turned off" is only meaningful once it thinks at all. */}
      {probe.respects_enabled && (
        <ThinkFinding
          ok={probe.respects_disabled}
          text={probe.respects_disabled ? t.settings.models.thinkDisables : t.settings.models.thinkIgnoresDisable}
        />
      )}
      {probe.thinks_by_default && (
        <div className="text-muted-foreground">
          {t.settings.models.thinkAlwaysOn}
        </div>
      )}
    </div>
  );
}

function ThinkFinding({ ok, text }: { ok: boolean; text: string }) {
  return (
    <div className="flex items-start gap-2">
      <span
        className={cn(
          "w-3.5 text-center font-bold",
          ok ? "text-green-600" : "text-amber-600",
        )}
      >
        {ok ? "✓" : "!"}
      </span>
      <span>{text}</span>
    </div>
  );
}

function Field({
  className,
  label,
  hint,
  children,
}: {
  className?: string;
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className={cn("space-y-2", className)}>
      <div className="text-sm font-medium">{label}</div>
      {children}
      {hint && <div className="text-muted-foreground text-xs">{hint}</div>}
    </div>
  );
}
