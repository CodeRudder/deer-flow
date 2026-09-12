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
import { useI18n } from "@/core/i18n/hooks";
import { ModelConfigRequestError } from "@/core/models/api";
import {
  useDeleteManagedModel,
  useManagedModels,
  useSaveManagedModel,
  useTestManagedModel,
} from "@/core/models/hooks";
import type {
  ManagedModel,
  ManagedModelWrite,
  ModelTestResult,
} from "@/core/models/types";

import { SettingsSection } from "./settings-section";

/** Form state for the add/edit panel. Every field is a string; blanks are unset. */
export interface ManagedModelDraft {
  name: string;
  use: string;
  model: string;
  display_name: string;
  api_key: string;
  api_key_value: string;
}

export const EMPTY_MODEL_DRAFT: ManagedModelDraft = {
  name: "",
  use: "",
  model: "",
  display_name: "",
  api_key: "",
  api_key_value: "",
};

/** `name`/`use`/`model` are required; the rest are optional. */
export function isManagedModelDraftComplete(draft: ManagedModelDraft): boolean {
  return [draft.name, draft.use, draft.model].every(
    (value) => value.trim().length > 0,
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

//: Server-owned / write-only fields that must never be written back verbatim.
const NON_ROUND_TRIP_KEYS = new Set([
  "index",
  "api_key_masked",
  "api_key",
  "api_key_value",
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
 * Build the wire payload for a save.
 *
 * Masked-key rule: when the user did not retype a key, `api_key` is omitted
 * entirely so the backend keeps the stored secret. `api_key_value` is the
 * write-only cleartext behind a *new* `$VAR` reference, so it is only sent
 * alongside a typed `$VAR` — a literal key carries its own secret.
 */
export function buildManagedModelWrite(
  draft: ManagedModelDraft,
  original?: ManagedModel,
): ManagedModelWrite {
  const write: ManagedModelWrite = {
    ...carriedOver(original),
    name: draft.name.trim(),
    use: draft.use.trim(),
    model: draft.model.trim(),
    display_name: draft.display_name.trim() || null,
  };
  const apiKey = draft.api_key.trim();
  if (apiKey) {
    write.api_key = apiKey;
    const cleartext = draft.api_key_value.trim();
    if (apiKey.startsWith("$") && cleartext) {
      write.api_key_value = cleartext;
    }
  }
  return write;
}

export function ModelSettingsPage() {
  const { t } = useI18n();
  const { models, isLoading, error } = useManagedModels();
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
        <ManagedModelList models={models} />
      )}
    </SettingsSection>
  );
}

function ManagedModelList({ models }: { models: ManagedModel[] }) {
  const { t } = useI18n();
  const { mutate: saveModel, isPending: isSaving } = useSaveManagedModel();
  const { mutate: deleteModel, isPending: isDeleting } =
    useDeleteManagedModel();
  const { mutate: testModel, isPending: isTesting } = useTestManagedModel();

  const [draft, setDraft] = useState<ManagedModelDraft | null>(null);
  const [editing, setEditing] = useState<ManagedModel | null>(null);
  const [pendingDelete, setPendingDelete] = useState<ManagedModel | null>(null);
  const [testResults, setTestResults] = useState<
    Record<string, ModelTestResult>
  >({});
  const [testingName, setTestingName] = useState<string | null>(null);

  const openCreate = () => {
    setEditing(null);
    setDraft({ ...EMPTY_MODEL_DRAFT });
  };

  const openEdit = (model: ManagedModel) => {
    setEditing(model);
    setDraft({
      ...EMPTY_MODEL_DRAFT,
      name: model.name,
      use: model.use,
      model: model.model,
      display_name: model.display_name ?? "",
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
    const payload = buildManagedModelWrite(draft, editing ?? undefined);
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
    // The probe accepts the stored entry as-is; reusing the read shape keeps the
    // stored secret in play (the backend resolves the masked/`$VAR` reference).
    const payload = buildManagedModelWrite(
      {
        name: model.name,
        use: model.use,
        model: model.model,
        display_name: model.display_name ?? "",
        api_key: "",
        api_key_value: "",
      },
      model,
    );
    testModel(payload, {
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
          onChange={setDraft}
          onClose={closeForm}
          onSave={handleSave}
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
  title,
  isSaving,
  onChange,
  onClose,
  onSave,
}: {
  draft: ManagedModelDraft;
  maskedKey: string | null;
  title: string;
  isSaving: boolean;
  onChange: (draft: ManagedModelDraft) => void;
  onClose: () => void;
  onSave: () => void;
}) {
  const { t } = useI18n();
  const complete = isManagedModelDraftComplete(draft);

  const field = (key: keyof ManagedModelDraft) => ({
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
        <Field label={t.settings.models.use}>
          <Input
            autoComplete="off"
            className="font-mono"
            placeholder={t.settings.models.usePlaceholder}
            {...field("use")}
          />
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
          hint={maskedKey ? t.settings.models.apiKeyKeepHint : undefined}
          label={t.settings.models.apiKey}
        >
          <Input
            autoComplete="off"
            className="font-mono"
            placeholder={maskedKey ?? t.settings.models.apiKeyPlaceholder}
            {...field("api_key")}
          />
        </Field>
        <Field label={t.settings.models.apiKeyValue}>
          <Input
            autoComplete="off"
            className="font-mono"
            placeholder={t.settings.models.apiKeyValuePlaceholder}
            {...field("api_key_value")}
          />
        </Field>
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

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-2">
      <div className="text-sm font-medium">{label}</div>
      {children}
      {hint && <div className="text-muted-foreground text-xs">{hint}</div>}
    </div>
  );
}
