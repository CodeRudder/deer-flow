"use client";

import {
  ArrowLeftIcon,
  BotIcon,
  CheckCircleIcon,
  InfoIcon,
  MoreHorizontalIcon,
  SaveIcon,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import {
  PromptInput,
  PromptInputFooter,
  PromptInputSubmit,
  PromptInputTextarea,
} from "@/components/ai-elements/prompt-input";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { ArtifactsProvider } from "@/components/workspace/artifacts";
import { MessageList } from "@/components/workspace/messages";
import { ThreadContext } from "@/components/workspace/messages/context";
import {
  MobileAuthField,
  MobileAuthLabel,
} from "@/components/workspace/mobile/auth-field";
import type { Agent } from "@/core/agents";
import {
  AgentNameCheckError,
  AgentsApiDisabledError,
  checkAgentName,
  getAgent,
} from "@/core/agents/api";
import { useI18n } from "@/core/i18n/hooks";
import { useThreadStream } from "@/core/threads/hooks";
import { uuid } from "@/core/utils/uuid";
import { isIMEComposing } from "@/lib/ime";
import { cn } from "@/lib/utils";

/**
 * Chat-based agent creation (G4) — the same conversation the desktop starts at
 * `/workspace/agents/new`, on a phone.
 *
 * The flow is the desktop page's, step for step, because it is the flow that
 * produces the agent: pick a name (validated with `checkAgentName` so a taken
 * name is an inline error rather than a 409 toast), then bootstrap the
 * conversation with `t.agents.nameStepBootstrapMessage`, then ask for the
 * actual write with `t.agents.saveCommandMessage` and watch for the
 * `setup_agent` tool to land. None of that copy is written here — it comes from
 * the same locale keys the desktop reads, so the two trees cannot drift.
 *
 * What differs is the phone's geometry: the name step is a full screen instead
 * of a centred card, the save action sits in a bottom sheet instead of a
 * dropdown, the composer is the mobile one and carries the bottom safe-area
 * inset itself (this screen is in `(fullbleed)` — no tab bar underneath).
 *
 * Two deliberate omissions, both mobile conventions rather than oversights:
 * the name field is **not** auto-focused (the mobile pages never pop the
 * keyboard on open), and the composer is not the chat page's `MobileComposer`
 * — this conversation is a fixed `flash` + `is_bootstrap` run with no model or
 * mode to choose, which is exactly the desktop's plain `PromptInput`.
 */

type Step = "name" | "chat";
type SetupAgentStatus = "idle" | "requested" | "completed";

const NAME_RE = /^[A-Za-z0-9-]+$/;
/** The desktop page's key, shared on purpose: the hint is shown once per user… */
const SAVE_HINT_STORAGE_KEY = "deerflow.agent-create.save-hint-seen";
/** …and this is the desktop's retry ladder, ported as-is (see `getAgentWithRetry`). */
const AGENT_READ_RETRY_DELAYS_MS = [200, 500, 1_000, 2_000];

function wait(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

async function getAgentWithRetry(agentName: string) {
  for (const delay of [0, ...AGENT_READ_RETRY_DELAYS_MS]) {
    if (delay > 0) {
      await wait(delay);
    }

    try {
      return await getAgent(agentName);
    } catch {
      // Retry until the write settles or the attempts are exhausted.
    }
  }

  return null;
}

export default function MobileNewAgentChatPage() {
  const { t } = useI18n();
  const router = useRouter();

  const [step, setStep] = useState<Step>("name");
  const [nameInput, setNameInput] = useState("");
  const [nameError, setNameError] = useState("");
  const [isCheckingName, setIsCheckingName] = useState(false);
  const [agentName, setAgentName] = useState("");
  const [agent, setAgent] = useState<Agent | null>(null);
  const [showSaveHint, setShowSaveHint] = useState(false);
  const [saveOpen, setSaveOpen] = useState(false);
  const [setupAgentStatus, setSetupAgentStatus] =
    useState<SetupAgentStatus>("idle");

  useEffect(() => {
    document.title = `${t.agents.createPageTitle} - ${t.pages.appName}`;
  }, [t.agents.createPageTitle, t.pages.appName]);

  const threadId = useMemo(() => uuid(), []);

  const { thread, sendMessage } = useThreadStream({
    threadId: undefined,
    context: {
      mode: "flash",
      is_bootstrap: true,
    },
    onFinish() {
      if (!agent && setupAgentStatus === "requested") {
        setSetupAgentStatus("idle");
      }
    },
    onToolEnd({ name }) {
      if (name !== "setup_agent" || !agentName) return;
      setSetupAgentStatus("completed");
      void getAgentWithRetry(agentName).then((fetched) => {
        if (fetched) {
          setAgent(fetched);
          return;
        }

        toast.error(t.agents.agentCreatedPendingRefresh);
      });
    },
  });

  useEffect(() => {
    if (typeof window === "undefined" || step !== "chat") {
      return;
    }
    if (window.localStorage.getItem(SAVE_HINT_STORAGE_KEY) === "1") {
      return;
    }
    setShowSaveHint(true);
    window.localStorage.setItem(SAVE_HINT_STORAGE_KEY, "1");
  }, [step]);

  const backHref = "/agents";

  const handleConfirmName = useCallback(async () => {
    const trimmed = nameInput.trim();
    if (!trimmed) return;
    if (!NAME_RE.test(trimmed)) {
      setNameError(t.agents.nameStepInvalidError);
      return;
    }

    setNameError("");
    setIsCheckingName(true);
    try {
      const result = await checkAgentName(trimmed);
      if (!result.available) {
        setNameError(t.agents.nameStepAlreadyExistsError);
        return;
      }
    } catch (err) {
      if (err instanceof AgentsApiDisabledError) {
        setNameError(t.agents.nameStepApiDisabledError);
      } else if (
        err instanceof AgentNameCheckError &&
        err.reason === "backend_unreachable"
      ) {
        setNameError(t.agents.nameStepNetworkError);
      } else if (
        err instanceof AgentNameCheckError &&
        err.reason === "request_failed"
      ) {
        // The backend-provided detail, wrapped in a localised prefix; see the
        // desktop page for why `err.message` is not usable here.
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
      setIsCheckingName(false);
    }

    setAgentName(trimmed);
    setStep("chat");
    await sendMessage(
      threadId,
      {
        text: t.agents.nameStepBootstrapMessage.replace("{name}", trimmed),
        files: [],
      },
      { agent_name: trimmed },
    );
  }, [
    nameInput,
    sendMessage,
    t.agents.nameStepAlreadyExistsError,
    t.agents.nameStepApiDisabledError,
    t.agents.nameStepNetworkError,
    t.agents.nameStepBootstrapMessage,
    t.agents.nameStepCheckError,
    t.agents.nameStepCheckErrorWithDetail,
    t.agents.nameStepInvalidError,
    threadId,
  ]);

  const handleNameKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" && !isIMEComposing(e)) {
      e.preventDefault();
      void handleConfirmName();
    }
  };

  const handleChatSubmit = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || thread.isLoading) return;
      await sendMessage(
        threadId,
        { text: trimmed, files: [] },
        { agent_name: agentName },
      );
    },
    [agentName, sendMessage, thread.isLoading, threadId],
  );

  const handleSaveAgent = useCallback(async () => {
    if (
      !agentName ||
      agent ||
      thread.isLoading ||
      setupAgentStatus !== "idle"
    ) {
      return;
    }

    setSaveOpen(false);
    setSetupAgentStatus("requested");
    setShowSaveHint(false);
    try {
      await sendMessage(
        threadId,
        { text: t.agents.saveCommandMessage, files: [] },
        { agent_name: agentName },
        { additionalKwargs: { hide_from_ui: true } },
      );
      toast.success(t.agents.saveRequested);
    } catch (error) {
      setSetupAgentStatus("idle");
      toast.error(error instanceof Error ? error.message : String(error));
    }
  }, [
    agent,
    agentName,
    sendMessage,
    setupAgentStatus,
    t.agents.saveCommandMessage,
    t.agents.saveRequested,
    thread.isLoading,
    threadId,
  ]);

  const header = (
    <header className="bg-background/95 sticky top-0 z-20 flex shrink-0 items-center gap-1 border-b px-1 py-1 supports-backdrop-filter:backdrop-blur">
      <Link
        href={backHref}
        aria-label={t.agents.title}
        data-testid="mobile-agent-create-back"
        className="active:bg-accent flex size-11 shrink-0 items-center justify-center rounded-full"
      >
        <ArrowLeftIcon aria-hidden="true" className="size-5" />
      </Link>
      <h1 className="min-w-0 flex-1 truncate px-1 text-[15px] font-medium">
        {t.agents.createPageTitle}
      </h1>

      {step === "chat" ? (
        <Sheet open={saveOpen} onOpenChange={setSaveOpen}>
          <Button
            type="button"
            variant="ghost"
            aria-label={t.agents.more}
            data-testid="mobile-agent-create-more"
            className="text-muted-foreground size-11 shrink-0 rounded-full p-0"
            onClick={() => setSaveOpen(true)}
          >
            <MoreHorizontalIcon aria-hidden="true" className="size-5" />
          </Button>
          <SheetContent
            side="bottom"
            data-testid="mobile-agent-create-menu"
            className="pb-[calc(env(safe-area-inset-bottom)+1rem)]"
          >
            <SheetHeader>
              <SheetTitle className="text-base">{t.agents.more}</SheetTitle>
            </SheetHeader>
            <div className="flex flex-col gap-1 px-2">
              <Button
                type="button"
                variant="ghost"
                data-testid="mobile-agent-create-save"
                disabled={
                  Boolean(agent) ||
                  thread.isLoading ||
                  setupAgentStatus !== "idle"
                }
                onClick={() => void handleSaveAgent()}
                className="min-h-12 justify-start gap-3 px-3 text-base font-normal"
              >
                <SaveIcon aria-hidden="true" className="size-4" />
                {setupAgentStatus === "requested"
                  ? t.agents.saving
                  : t.agents.save}
              </Button>
            </div>
          </SheetContent>
        </Sheet>
      ) : null}
    </header>
  );

  if (step === "name") {
    return (
      <div className="flex min-h-full flex-col">
        {header}
        <main className="flex flex-1 flex-col items-center justify-center px-4 pb-10">
          <div className="w-full max-w-sm space-y-8">
            <div className="space-y-3 text-center">
              <div className="bg-primary/10 mx-auto flex size-14 items-center justify-center rounded-full">
                <BotIcon aria-hidden="true" className="text-primary size-7" />
              </div>
              <div className="space-y-1">
                <h2 className="text-xl font-semibold">
                  {t.agents.nameStepTitle}
                </h2>
                <p className="text-muted-foreground text-sm">
                  {t.agents.nameStepHint}
                </p>
              </div>
            </div>

            <div className="space-y-3">
              <MobileAuthLabel htmlFor="mobile-agent-create-name">
                {t.agents.nameLabel}
              </MobileAuthLabel>
              <MobileAuthField
                id="mobile-agent-create-name"
                data-testid="mobile-agent-create-name"
                placeholder={t.agents.nameStepPlaceholder}
                value={nameInput}
                onChange={(e) => {
                  setNameInput(e.target.value);
                  setNameError("");
                }}
                onKeyDown={handleNameKeyDown}
                disabled={isCheckingName}
                // A slug, not prose: iOS would capitalise the first letter and
                // autocorrect the hyphens away.
                autoCapitalize="none"
                autoCorrect="off"
                autoComplete="off"
                spellCheck={false}
                className={cn(nameError && "border-destructive")}
              />
              {nameError ? (
                <p role="alert" className="text-destructive text-sm">
                  {nameError}
                </p>
              ) : null}
              <Button
                className="min-h-12 w-full text-base"
                data-testid="mobile-agent-create-name-submit"
                onClick={() => void handleConfirmName()}
                disabled={!nameInput.trim() || isCheckingName}
              >
                {t.agents.nameStepContinue}
              </Button>
            </div>
          </div>
        </main>
      </div>
    );
  }

  return (
    <ThreadContext.Provider value={{ thread }}>
      <ArtifactsProvider>
        <div className="flex h-full min-h-0 flex-col">
          {header}

          <main className="flex min-h-0 flex-1 flex-col">
            {showSaveHint ? (
              <div className="shrink-0 px-3 pt-3">
                <Alert data-testid="mobile-agent-create-hint">
                  <InfoIcon aria-hidden="true" className="size-4" />
                  <AlertDescription>{t.agents.saveHint}</AlertDescription>
                </Alert>
              </div>
            ) : null}

            <div className="flex min-h-0 flex-1 justify-center">
              <MessageList
                className="size-full"
                threadId={threadId}
                thread={thread}
              />
            </div>

            {/* This screen owns the bottom edge (no tab bar in `(fullbleed)`),
                so the composer carries the safe-area inset itself — the same
                rule `composer.tsx` follows. */}
            <div className="bg-background flex shrink-0 justify-center border-t px-3 pt-3 pb-[calc(env(safe-area-inset-bottom)+0.75rem)]">
              <div className="w-full">
                {agent ? (
                  <div className="flex flex-col items-center gap-4 rounded-2xl border py-8 text-center">
                    <CheckCircleIcon
                      aria-hidden="true"
                      className="text-primary size-10"
                    />
                    <p className="font-semibold">{t.agents.agentCreated}</p>
                    <div className="flex gap-2">
                      <Button
                        className="min-h-12 text-base"
                        data-testid="mobile-agent-create-start-chat"
                        onClick={() =>
                          router.push(
                            `/workspace/agents/${agentName}/chats/new`,
                          )
                        }
                      >
                        {t.agents.startChatting}
                      </Button>
                      <Button
                        variant="outline"
                        className="min-h-12 text-base"
                        onClick={() => router.push(backHref)}
                      >
                        {t.agents.backToGallery}
                      </Button>
                    </div>
                  </div>
                ) : (
                  <PromptInput
                    onSubmit={({ text }) => void handleChatSubmit(text)}
                  >
                    <PromptInputTextarea
                      // 16px is the iOS floor (<16px zooms the page on focus).
                      className="max-h-40 min-h-11 py-2 text-base md:text-base"
                      data-testid="mobile-agent-create-input"
                      placeholder={t.agents.createPageSubtitle}
                      disabled={thread.isLoading}
                      // Never auto-focused: the keyboard would cover the
                      // conversation this screen exists to show.
                      autoFocus={false}
                    />
                    <PromptInputFooter className="justify-end">
                      <PromptInputSubmit
                        className="size-11"
                        data-testid="mobile-agent-create-send"
                        disabled={thread.isLoading}
                      />
                    </PromptInputFooter>
                  </PromptInput>
                )}
              </div>
            </div>
          </main>
        </div>
      </ArtifactsProvider>
    </ThreadContext.Provider>
  );
}
