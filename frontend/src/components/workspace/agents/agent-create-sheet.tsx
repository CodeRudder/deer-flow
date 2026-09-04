"use client";

import { BotIcon, SaveIcon } from "lucide-react";
import { type ReactNode, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Textarea } from "@/components/ui/textarea";
import { useCreateAgent } from "@/core/agents";
import {
  AgentNameCheckError,
  AgentsApiDisabledError,
  checkAgentName,
} from "@/core/agents/api";
import { useI18n } from "@/core/i18n/hooks";
import { useModels } from "@/core/models/hooks";
import { cn } from "@/lib/utils";

import { MODEL_DEFAULT_VALUE } from "./agent-edit-sheet";

/** Mirrors the backend AGENT_NAME_PATTERN and the bootstrap flow's name rule. */
export function isValidAgentName(name: string): boolean {
  return /^[A-Za-z0-9-]+$/.test(name);
}

interface AgentCreateOptionProps {
  icon: ReactNode;
  title: string;
  description: string;
  onClick: () => void;
}

/**
 * One choice row inside the "new agent" dialog (manual form vs. chat flow).
 * Pure and provider-free so unit tests can render it directly.
 */
export function AgentCreateOption({
  icon,
  title,
  description,
  onClick,
}: AgentCreateOptionProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="hover:bg-accent/50 flex w-full items-start gap-3 rounded-lg border p-4 text-left transition-colors"
    >
      <div className="bg-primary/10 text-primary mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg">
        {icon}
      </div>
      <div className="min-w-0">
        <p className="text-sm font-medium">{title}</p>
        <p className="text-muted-foreground mt-0.5 text-sm">{description}</p>
      </div>
    </button>
  );
}

interface AgentCreateSheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * Manual agent creation: name, description, model, and SOUL.md in one form.
 * The chat-based flow (/workspace/agents/new) stays untouched — the gallery's
 * "new agent" dialog decides between the two. SOUL.md may be left empty
 * (existing semantics: no soul until the edit panel fills it in).
 */
export function AgentCreateSheet({
  open,
  onOpenChange,
}: AgentCreateSheetProps) {
  const { t } = useI18n();

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex size-full flex-col gap-0 overflow-hidden sm:max-w-xl">
        <SheetHeader className="shrink-0">
          <SheetTitle className="flex items-center gap-2">
            <BotIcon className="text-primary h-4 w-4" />
            {t.agents.createTitle}
          </SheetTitle>
          <SheetDescription>{t.agents.createDescription}</SheetDescription>
        </SheetHeader>
        {/* Conditional render so every open starts from a fresh form state. */}
        {open && <AgentCreateForm onDone={() => onOpenChange(false)} />}
      </SheetContent>
    </Sheet>
  );
}

function AgentCreateForm({ onDone }: { onDone: () => void }) {
  const { t } = useI18n();
  const createAgent = useCreateAgent();
  const { models } = useModels();

  const [name, setName] = useState("");
  const [nameError, setNameError] = useState("");
  const [checkingName, setCheckingName] = useState(false);
  const [description, setDescription] = useState("");
  const [model, setModel] = useState(MODEL_DEFAULT_VALUE);
  const [soul, setSoul] = useState("");

  const busy = checkingName || createAgent.isPending;

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
      onDone();
    } catch (err) {
      if (err instanceof AgentsApiDisabledError) {
        toast.error(t.agents.apiDisabledError);
      } else {
        toast.error(err instanceof Error ? err.message : String(err));
      }
    }
  }

  return (
    <>
      <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-4 pb-4">
        <div className="space-y-2">
          <label className="text-sm font-medium" htmlFor="agent-create-name">
            {t.agents.nameLabel}
          </label>
          <Input
            id="agent-create-name"
            value={name}
            onChange={(e) => handleNameChange(e.target.value)}
            placeholder={t.agents.nameStepPlaceholder}
            disabled={busy}
            className={cn(nameError && "border-destructive")}
          />
          <p className="text-muted-foreground text-xs">
            {t.agents.nameStepHint}
          </p>
          {nameError ? (
            <p className="text-destructive text-sm">{nameError}</p>
          ) : null}
        </div>

        <div className="space-y-2">
          <label
            className="text-sm font-medium"
            htmlFor="agent-create-description"
          >
            {t.agents.descriptionLabel}
          </label>
          <Input
            id="agent-create-description"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            disabled={busy}
          />
        </div>

        <div className="space-y-2">
          <label className="text-sm font-medium" htmlFor="agent-create-model">
            {t.agents.modelLabel}
          </label>
          <Select value={model} onValueChange={setModel} disabled={busy}>
            <SelectTrigger id="agent-create-model" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={MODEL_DEFAULT_VALUE}>
                {t.agents.modelDefault}
              </SelectItem>
              {models.map((m) => (
                <SelectItem key={m.name} value={m.name}>
                  {m.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="flex min-h-56 flex-1 flex-col gap-2">
          <label className="text-sm font-medium" htmlFor="agent-create-soul">
            {t.agents.soulLabel}
          </label>
          <Textarea
            id="agent-create-soul"
            value={soul}
            onChange={(e) => setSoul(e.target.value)}
            disabled={busy}
            placeholder={t.agents.createSoulPlaceholder}
            className="min-h-56 flex-1 resize-none font-mono text-xs"
          />
        </div>
      </div>

      <SheetFooter className="shrink-0 border-t">
        <Button variant="outline" onClick={() => onDone()} disabled={busy}>
          {t.common.cancel}
        </Button>
        <Button
          onClick={() => void handleCreate()}
          disabled={!name.trim() || busy}
        >
          <SaveIcon className="mr-1.5 h-4 w-4" />
          {busy ? t.common.loading : t.common.create}
        </Button>
      </SheetFooter>
    </>
  );
}
