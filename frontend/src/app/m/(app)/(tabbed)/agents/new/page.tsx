"use client";

import { ArrowLeftIcon, SaveIcon } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { isValidAgentName } from "@/components/workspace/agents/agent-create-sheet";
import { MODEL_DEFAULT_VALUE } from "@/components/workspace/agents/agent-edit-sheet";
import {
  MobileAgentDescriptionField,
  MobileAgentSoulField,
} from "@/components/workspace/mobile/agents/agent-form-fields";
import { MobileAgentModelPicker } from "@/components/workspace/mobile/agents/agent-model-picker";
import {
  MobileAuthField,
  MobileAuthLabel,
} from "@/components/workspace/mobile/auth-field";
import { useCreateAgent } from "@/core/agents";
import {
  AgentNameCheckError,
  AgentsApiDisabledError,
  checkAgentName,
} from "@/core/agents/api";
import { useI18n } from "@/core/i18n/hooks";
import { useModels } from "@/core/models/hooks";
import { cn } from "@/lib/utils";

/**
 * Manual agent creation, as a full screen (prototype ⑥'s `＋` → 手动创建).
 *
 * The desktop does the same thing in a right-side Sheet
 * (`agent-create-sheet.tsx`); the plan moves it to a full screen because
 * SOUL.md is a long document and a phone sheet leaves it a few lines tall.
 *
 * Everything below the layout is the desktop form's: `isValidAgentName` +
 * `checkAgentName` before the request (so an existing name becomes an inline
 * error instead of a 409 toast), the same `AgentNameCheckError` /
 * `AgentsApiDisabledError` mapping, `useCreateAgent` (whose `onSuccess`
 * invalidates the `["agents"]` query, so the gallery is fresh by the time this
 * screen navigates back to it), and the shared `MODEL_DEFAULT_VALUE` sentinel
 * that turns "follow default model" into `model: null` on the wire.
 *
 * The submit handler is a deliberate line-by-line port: the desktop's lives
 * inside its Sheet component and is not exported, and the plan requires the
 * validation to stay identical rather than be reinvented.
 */
export default function MobileNewAgentPage() {
  const { t } = useI18n();
  const router = useRouter();
  const createAgent = useCreateAgent();
  const { models } = useModels();

  const [name, setName] = useState("");
  const [nameError, setNameError] = useState("");
  const [checkingName, setCheckingName] = useState(false);
  const [description, setDescription] = useState("");
  const [model, setModel] = useState(MODEL_DEFAULT_VALUE);
  const [soul, setSoul] = useState("");

  const busy = checkingName || createAgent.isPending;

  useEffect(() => {
    document.title = `${t.agents.createTitle} - ${t.pages.appName}`;
  }, [t.agents.createTitle, t.pages.appName]);

  function handleNameChange(value: string) {
    setName(value);
    if (nameError) {
      setNameError("");
    }
  }

  async function handleCreate() {
    const trimmed = name.trim();
    if (!trimmed || busy) {
      return;
    }
    if (!isValidAgentName(trimmed)) {
      setNameError(t.agents.nameStepInvalidError);
      return;
    }

    // Pre-check availability so the user sees a friendly inline error instead
    // of the backend's 409 toast; error mapping mirrors the bootstrap flow.
    setNameError("");
    setCheckingName(true);
    try {
      const result = await checkAgentName(trimmed);
      if (!result.available) {
        setNameError(t.agents.nameStepAlreadyExistsError);
        return;
      }
    } catch (err) {
      if (err instanceof AgentsApiDisabledError) {
        setNameError(t.agents.apiDisabledError);
      } else if (
        err instanceof AgentNameCheckError &&
        err.reason === "backend_unreachable"
      ) {
        setNameError(t.agents.nameStepNetworkError);
      } else if (err instanceof AgentNameCheckError) {
        setNameError(
          err.detail
            ? t.agents.nameStepCheckErrorWithDetail.replace(
                "{detail}",
                err.detail,
              )
            : t.agents.nameStepCheckError,
        );
      } else {
        setNameError(t.agents.nameStepCheckError);
      }
      return;
    } finally {
      setCheckingName(false);
    }

    try {
      await createAgent.mutateAsync({
        name: trimmed,
        description,
        model: model === MODEL_DEFAULT_VALUE ? null : model,
        soul,
      });
      toast.success(t.agents.createSuccess);
      // Public path: the middleware re-lands a phone on the mobile gallery, so
      // `/m/` never reaches the address bar.
      router.push("/agents");
    } catch (err) {
      if (err instanceof AgentsApiDisabledError) {
        toast.error(t.agents.apiDisabledError);
      } else {
        toast.error(err instanceof Error ? err.message : String(err));
      }
    }
  }

  return (
    <div className="flex min-h-full flex-col">
      <header className="bg-background/95 sticky top-0 z-10 flex items-center gap-1 border-b px-1 py-1 supports-backdrop-filter:backdrop-blur">
        <Link
          href="/agents"
          aria-label={t.agents.title}
          data-testid="mobile-new-agent-back"
          className="active:bg-accent flex size-11 shrink-0 items-center justify-center rounded-full"
        >
          <ArrowLeftIcon aria-hidden="true" className="size-5" />
        </Link>
        <h1 className="min-w-0 flex-1 truncate px-1 text-base font-medium">
          {t.agents.createTitle}
        </h1>
      </header>

      <div className="flex flex-1 flex-col gap-4 px-4 py-4">
        <div>
          <MobileAuthLabel htmlFor="mobile-agent-name">
            {t.agents.nameLabel}
          </MobileAuthLabel>
          <MobileAuthField
            id="mobile-agent-name"
            data-testid="mobile-agent-name"
            value={name}
            onChange={(event) => handleNameChange(event.target.value)}
            placeholder={t.agents.nameStepPlaceholder}
            disabled={busy}
            // A slug, not prose: iOS would otherwise capitalise the first
            // letter and autocorrect the hyphens away.
            autoCapitalize="none"
            autoCorrect="off"
            autoComplete="off"
            spellCheck={false}
            className={cn(nameError && "border-destructive")}
          />
          <p className="text-muted-foreground mt-1.5 text-xs">
            {t.agents.nameStepHint}
          </p>
          {nameError ? (
            <p role="alert" className="text-destructive mt-1 text-sm">
              {nameError}
            </p>
          ) : null}
        </div>

        <MobileAgentDescriptionField
          id="mobile-agent-description"
          value={description}
          onChange={setDescription}
          disabled={busy}
        />

        <div>
          <MobileAuthLabel htmlFor="mobile-agent-model">
            {t.agents.modelLabel}
          </MobileAuthLabel>
          <MobileAgentModelPicker
            id="mobile-agent-model"
            value={model}
            options={models.map((entry) => entry.name)}
            onChange={setModel}
            disabled={busy}
          />
        </div>

        <MobileAgentSoulField
          id="mobile-agent-soul"
          value={soul}
          onChange={setSoul}
          disabled={busy}
        />
      </div>

      {/* Sticky under `<main>`'s scroll container so the primary action never
          scrolls away from the thumb. */}
      {/* The bottom edge belongs to the tab bar below `<main>` (T17), which is
          where the safe-area inset is honoured — so this bar keeps plain
          padding and sits directly above it. */}
      <div className="bg-background sticky bottom-0 z-10 flex gap-2 border-t p-4 supports-backdrop-filter:backdrop-blur">
        <Button
          variant="outline"
          className="min-h-12 flex-1 text-base"
          onClick={() => router.push("/agents")}
          disabled={busy}
        >
          {t.common.cancel}
        </Button>
        <Button
          className="min-h-12 flex-1 text-base"
          data-testid="mobile-agent-create-submit"
          onClick={() => void handleCreate()}
          disabled={!name.trim() || busy}
        >
          <SaveIcon aria-hidden="true" className="size-4" />
          {busy ? t.common.loading : t.common.create}
        </Button>
      </div>
    </div>
  );
}
