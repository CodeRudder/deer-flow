"use client";

import type { Message } from "@langchain/langgraph-sdk";
import type { ChatStatus } from "ai";
import { ChevronDownIcon, PlusIcon, SparklesIcon, XIcon } from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type ReactNode,
} from "react";
import { toast } from "sonner";

import {
  PromptInput,
  PromptInputFooter,
  PromptInputSubmit,
  PromptInputTextarea,
  PromptInputTools,
  usePromptInputAttachments,
  usePromptInputController,
  type PromptInputMessage,
} from "@/components/ai-elements/prompt-input";
import { Button } from "@/components/ui/button";
import { useI18n } from "@/core/i18n/hooks";
import { useModels } from "@/core/models/hooks";
import { ModelProviderLogo } from "@/core/models/logo";
import { useSkills } from "@/core/skills/hooks";
import type { AgentThreadContext } from "@/core/threads";
import { splitUnsupportedUploadFiles } from "@/core/uploads";
import { cn } from "@/lib/utils";

import { resolveMode, type ComposerMode } from "./composer-mode";
import {
  ComposerModeSheet,
  ComposerModelPicker,
  MODE_ICONS,
} from "./composer-pickers";
import { ComposerSheet } from "./composer-sheet";
import { useFollowups } from "./use-followups";

/**
 * Per-thread composer settings.
 *
 * Spelled out the same way the desktop `InputBox` spells it rather than as
 * `LocalSettings["context"]`: the `Omit` there collapses onto
 * `AgentThreadContext`'s index signature, which would type every reused field
 * as `unknown`.
 */
export type ComposerContext = Omit<
  AgentThreadContext,
  "thread_id" | "is_plan_mode" | "thinking_enabled" | "subagent_enabled"
> & {
  model_name?: string | undefined;
  mode: ComposerMode | undefined;
  reasoning_effort?: "minimal" | "low" | "medium" | "high";
  image_generation_model?: string;
  video_generation_model?: string;
};

/** Leading `/query` of the composer text, or null when it is not a slash token. */
export function leadingSlashQuery(value: string): string | null {
  if (!value.startsWith("/")) {
    return null;
  }
  const query = value.slice(1);
  return query.includes("/") || /\s/.test(query) ? null : query;
}

const MAX_SKILL_SUGGESTIONS = 6;

/**
 * A 44px square control for the main row. The desktop's toolbar buttons are
 * 32px (`size-8`), which is below the touch floor the plan sets for phones.
 */
const ROW_BUTTON_CLASS = "size-11 shrink-0 rounded-full p-0";

/**
 * The persistent mode/model pills (T16, prototype ②'s `.crow .pill`).
 *
 * The plan asks for a 34px visual inside a ≥44px touch target, which is why the
 * button is the 44px box and the bordered pill is the span inside it — one
 * element cannot be both. `max-w-[134px]` and `min-w-0` are the prototype's own
 * numbers: they are what keeps a long model name (`DeepSeek-v4.1-Flash`) from
 * pushing the row past 390px, with `truncate` ellipsising whatever is left.
 */
const PILL_BUTTON_CLASS = "flex min-h-11 min-w-0 items-center";
const PILL_CLASS =
  "border-border bg-card flex min-h-[34px] min-w-0 max-w-[134px] items-center gap-1 rounded-full border px-2.5 text-[12.5px]";

/**
 * One pill. A plain function rather than a component so it can be handed
 * straight to `ModelSelectorTrigger asChild`, which clones its child and needs
 * a real DOM node to clone onto.
 *
 * `title` carries the full value: the pill truncates by design, and on a touch
 * screen a long-press on the title is the only way to read the rest.
 */
function composerPill({
  testId,
  icon,
  label,
  ariaLabel,
  className,
  onClick,
}: {
  testId: string;
  icon: ReactNode;
  label: string;
  ariaLabel: string;
  className?: string;
  onClick?: () => void;
}) {
  return (
    <button
      type="button"
      data-testid={testId}
      aria-label={ariaLabel}
      title={label}
      onClick={onClick}
      className={cn(PILL_BUTTON_CLASS, className)}
    >
      <span className={PILL_CLASS}>
        {icon}
        <span className="truncate">{label}</span>
        <ChevronDownIcon
          aria-hidden="true"
          className="text-muted-foreground size-3 shrink-0"
        />
      </span>
    </button>
  );
}

type MobileComposerProps = {
  status?: ChatStatus;
  threadId: string;
  /** The live transcript — read by the follow-up request only. */
  messages: Message[];
  context: ComposerContext;
  disabled?: boolean;
  /** Shown instead of the placeholder while `disabled` (clarification pending). */
  disabledPlaceholder?: string;
  onContextChange: (context: ComposerContext) => void;
  onSubmit: (message: PromptInputMessage) => void | Promise<void>;
  onStop: () => void;
};

/**
 * Mobile composer (prototype ②): the main row is ＋ / mode pill / model pill /
 * send, with the textarea above it.
 *
 * It is built from the same `PromptInput` primitives the desktop `InputBox`
 * uses — the attachment pipeline, the Enter-to-send handling and the
 * submit/stop button all come from `@/components/ai-elements/prompt-input` —
 * but none of the desktop's controls are rendered here. Mode and model are the
 * two pills, showing the values the next request will carry; everything else
 * the row cannot hold (the image/video generation models, reasoning effort,
 * plan mode, the four attachments) lives in the `＋` sheet
 * (`composer-sheet.tsx`), because those controls cannot share one row and
 * still hold the 44px touch floor at 390px.
 *
 * Requires a `PromptInputProvider` above it, exactly like the desktop page:
 * `useSpecificChatMode()` (called by `useChatPage()`) reads the prompt-input
 * controller, and the `＋` sheet's pickers feed the same attachment state.
 */
export function MobileComposer({
  status = "ready",
  threadId,
  messages,
  context,
  disabled,
  disabledPlaceholder,
  onContextChange,
  onSubmit,
  onStop,
}: MobileComposerProps) {
  const { t } = useI18n();
  const { models, visionModels } = useModels();
  const { skills } = useSkills();
  const { textInput } = usePromptInputController();
  const attachments = usePromptInputAttachments();
  const [sheetOpen, setSheetOpen] = useState(false);
  const [modeSheetOpen, setModeSheetOpen] = useState(false);
  const galleryInputRef = useRef<HTMLInputElement | null>(null);
  const cameraInputRef = useRef<HTMLInputElement | null>(null);
  const {
    followups: followupSuggestions,
    loading: followupsLoading,
    visible: followupsVisible,
    dismiss: dismissFollowups,
  } = useFollowups({
    threadId,
    messages,
    isStreaming: status === "streaming",
    disabled: disabled ?? false,
  });

  const selectedModel = useMemo(
    () =>
      models.find((model) => model.name === context.model_name) ?? models[0],
    [context.model_name, models],
  );
  const supportThinking = selectedModel?.supports_thinking ?? false;
  const supportReasoningEffort =
    selectedModel?.supports_reasoning_effort ?? false;
  const resolvedMode = resolveMode(context.mode, supportThinking);

  // What the two pills label themselves with. Both show the values the request
  // will carry rather than the raw context: an unpinned model resolves to the
  // first configured one (the effect above pins that too), and an unpinned mode
  // to `resolvedMode`. A missing model list falls back to the generic label so
  // the row never renders an empty pill.
  const ModeIcon = MODE_ICONS[resolvedMode];
  const modeLabels = {
    flash: t.inputBox.flashMode,
    thinking: t.inputBox.reasoningMode,
    pro: t.inputBox.proMode,
    ultra: t.inputBox.ultraMode,
  } satisfies Record<ComposerMode, string>;
  const modeLabel = modeLabels[resolvedMode];
  const modelLabel =
    selectedModel?.display_name ?? selectedModel?.name ?? t.inputBox.model;

  // Pin the resolved default the way the desktop input box does: no pinned
  // model means "the first configured model stands in", and the mode follows
  // that model's thinking capability. Both halves have to be written, not just
  // displayed — the `＋` sheet's 模型 row shows `models[0]` and its 模式 /
  // 计划模式 rows show their resolved values, so the request must carry exactly
  // those or the panel would describe a run the backend never performs (the
  // backend derives `thinking_enabled` / `is_plan_mode` from the mode).
  //
  // Gated on `models.length` for the same reason the desktop is: until the
  // model list lands, "supports thinking" is unknown and the resolution would
  // pin Flash, which the effect would then never revisit (mode is no longer
  // undefined). `handleSubmit` covers the still-unresolved case at send time.
  useEffect(() => {
    if (models.length === 0) {
      return;
    }
    const currentModel =
      models.find((model) => model.name === context.model_name) ?? models[0]!;
    const nextMode = resolveMode(
      context.mode,
      currentModel.supports_thinking ?? false,
    );

    if (context.model_name === currentModel.name && context.mode === nextMode) {
      return;
    }
    onContextChange({
      ...context,
      model_name: currentModel.name,
      mode: nextMode,
    });
  }, [context, models, onContextChange]);

  // A vision model that has left the provider list must not stay pinned: the
  // `＋` sheet falls back to the first configured one for its label, so a stale
  // name would show one model and request another. Same clear-on-missing rule
  // as the desktop picker.
  useEffect(() => {
    if (!context.vision_model_name) {
      return;
    }
    if (
      visionModels.some((model) => model.name === context.vision_model_name)
    ) {
      return;
    }
    onContextChange({ ...context, vision_model_name: undefined });
  }, [context, onContextChange, visionModels]);

  const handlePicked = useCallback(
    (event: ChangeEvent<HTMLInputElement>) => {
      // Snapshot the selection *before* resetting `value`. `input.files` hands
      // back the same `FileList` object until the selection changes, and
      // clearing `value` is exactly such a change — so a reference taken first
      // would be the object this line just emptied, and every photo would be
      // dropped silently. (`prompt-input.tsx`'s own hidden input for 文件
      // snapshots the same way, which is why that entry works.)
      const files = Array.from(event.target.files ?? []);
      // Allow re-picking the same file.
      event.target.value = "";
      if (files.length === 0) {
        return;
      }
      // Same guard as the primitive's own hidden input: `.app` bundles are
      // directories that cannot be uploaded.
      const { accepted, message } = splitUnsupportedUploadFiles(files);
      if (message) {
        toast.error(message);
      }
      if (accepted.length > 0) {
        attachments.add(accepted);
      }
    },
    [attachments],
  );

  const slashQuery = leadingSlashQuery(textInput.value ?? "");
  const skillSuggestions = useMemo(() => {
    if (slashQuery === null) {
      return [];
    }
    const query = slashQuery.toLowerCase();
    return skills
      .map((skill, index) => ({ skill, index, name: skill.name.toLowerCase() }))
      .filter(({ skill, name }) => skill.enabled && name.includes(query))
      .sort((a, b) => {
        const aStarts = a.name.startsWith(query);
        const bStarts = b.name.startsWith(query);
        if (aStarts !== bStarts) {
          return aStarts ? -1 : 1;
        }
        return a.index - b.index;
      })
      .slice(0, MAX_SKILL_SUGGESTIONS)
      .map(({ skill }) => skill);
  }, [skills, slashQuery]);
  const showSkillSuggestions = !disabled && skillSuggestions.length > 0;

  /**
   * A tapped chip fills the composer; it deliberately does not send.
   *
   * The desktop submits straight away when its box is empty and asks
   * append/replace in a dialog otherwise (`input-box.tsx`
   * `handleFollowupClick`). Neither half survives the translation: a phone has
   * no dialog here, and an accidental send is worse on a tap interface than one
   * extra tap on the send button — so the chip only carries the text across,
   * and the row gets out of the way.
   */
  const handleFollowupClick = useCallback(
    (suggestion: string) => {
      if (status === "streaming") {
        return;
      }
      textInput.setInput(suggestion);
      dismissFollowups();
    },
    [dismissFollowups, status, textInput],
  );

  const handleSubmit = useCallback(
    (message: PromptInputMessage) => {
      // The send button doubles as stop while a run streams (same contract as
      // the desktop input box).
      if (status === "streaming") {
        onStop();
        return;
      }
      if (!message.text.trim() && message.files.length === 0) {
        return;
      }
      // The thread context is persisted per thread; make sure the resolved mode
      // is flushed before the request reads it.
      if (context.mode === undefined) {
        onContextChange({ ...context, mode: resolvedMode });
        return new Promise<void>((resolve, reject) => {
          setTimeout(() => {
            Promise.resolve(onSubmit(message)).then(resolve).catch(reject);
          }, 0);
        });
      }
      return onSubmit(message);
    },
    [context, onContextChange, onSubmit, onStop, resolvedMode, status],
  );

  return (
    <div
      data-testid="mobile-composer"
      // The composer owns the bottom edge: the thread route lives in
      // `(app)/(fullbleed)`, so there is no tab bar underneath to carry the
      // inset for it (prototype ② draws it straight onto the home indicator).
      // On the tabbed roots this element is not rendered at all, so the inset
      // can never be applied twice.
      className="bg-background/95 shrink-0 border-t px-2 pt-1.5 pb-[calc(0.375rem+env(safe-area-inset-bottom))] supports-backdrop-filter:backdrop-blur"
    >
      {showSkillSuggestions && (
        <div
          role="listbox"
          aria-label={t.inputBox.skillCommands}
          data-testid="mobile-skill-suggestions"
          className="bg-card mb-1.5 flex flex-col rounded-2xl border p-1 shadow-lg"
        >
          {skillSuggestions.map((skill) => (
            <button
              key={skill.name}
              type="button"
              role="option"
              aria-selected={false}
              // Keep focus in the textarea so the keyboard does not close on tap.
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => {
                textInput.setInput(`/${skill.name} `);
              }}
              className="active:bg-accent flex min-h-11 items-center gap-2 rounded-xl px-3 text-left"
            >
              <SparklesIcon
                aria-hidden="true"
                className="text-muted-foreground size-4 shrink-0"
              />
              <span className="min-w-0 flex-1 truncate text-base">
                /{skill.name}
              </span>
            </button>
          ))}
        </div>
      )}

      {/* Follow-up chips (C10). The row scrolls sideways rather than wrapping
          (prototype ②'s `.chips` / `.chip`): five suggestions at 390px would
          otherwise stack into four or five lines and push the transcript off
          the screen. */}
      {followupsVisible && !showSkillSuggestions && (
        <div
          data-testid="mobile-followups"
          className="mb-1.5 flex items-center gap-1"
        >
          <div
            data-testid="mobile-followups-row"
            className="flex min-w-0 flex-1 items-center gap-2 overflow-x-auto pb-0.5"
          >
            {followupsLoading ? (
              <span className="text-muted-foreground bg-background/80 shrink-0 rounded-full border px-3.5 py-1.5 text-xs">
                {t.inputBox.followupLoading}
              </span>
            ) : (
              followupSuggestions.map((suggestion) => (
                <button
                  key={suggestion}
                  type="button"
                  className="border-border bg-card active:bg-accent flex min-h-11 shrink-0 items-center rounded-full border px-3.5 text-sm whitespace-nowrap"
                  onClick={() => handleFollowupClick(suggestion)}
                >
                  {suggestion}
                </button>
              ))
            )}
          </div>
          <button
            type="button"
            aria-label={t.common.close}
            data-testid="mobile-followups-dismiss"
            className="text-muted-foreground active:bg-accent flex size-11 shrink-0 items-center justify-center rounded-full"
            onClick={dismissFollowups}
          >
            <XIcon aria-hidden="true" className="size-4" />
          </button>
        </div>
      )}

      <PromptInput
        className={cn(
          "bg-background/85 rounded-2xl backdrop-blur-sm *:data-[slot='input-group']:rounded-2xl",
        )}
        multiple
        onSubmit={handleSubmit}
      >
        {attachments.files.length > 0 && (
          <div className="flex flex-wrap gap-2 px-3 pt-3">
            {attachments.files.map((file) => (
              <span
                key={file.id}
                data-testid="mobile-composer-attachment"
                className="bg-muted flex items-center gap-1 rounded-lg py-0.5 pr-0.5 pl-2"
              >
                <span className="max-w-32 truncate text-sm">
                  {file.filename}
                </span>
                {/* The primitive's `PromptInputAttachment` reveals its remove
                    button on hover only; on touch that is unreachable, so the
                    mobile chip keeps it permanently visible. */}
                <button
                  type="button"
                  aria-label={t.common.delete}
                  onClick={() => attachments.remove(file.id)}
                  className="text-muted-foreground active:bg-accent flex size-11 items-center justify-center rounded-md"
                >
                  <XIcon aria-hidden="true" className="size-4" />
                </button>
              </span>
            ))}
          </div>
        )}
        <PromptInputTextarea
          // 16px is the iOS floor (<16px makes Safari zoom the page on focus,
          // and `md:text-sm` would reintroduce it on a landscape phone).
          className="max-h-40 min-h-11 py-2 text-base md:text-base"
          // Deliberately never auto-focused: on a phone the keyboard would
          // cover half the transcript the moment the page opens.
          autoFocus={false}
          disabled={disabled}
          placeholder={
            disabled && disabledPlaceholder
              ? disabledPlaceholder
              : t.inputBox.placeholder
          }
        />
        <PromptInputFooter className="gap-2">
          {/* `min-w-0 flex-1`: the pills must be free to shrink, otherwise a
              long model name would push the send button off a 360px screen
              instead of ellipsising inside its own pill. */}
          <PromptInputTools className="min-w-0 flex-1">
            <Button
              type="button"
              variant="ghost"
              aria-label={t.inputBox.composerOptions}
              data-testid="mobile-composer-plus"
              className={cn(ROW_BUTTON_CLASS, "text-muted-foreground")}
              onClick={() => setSheetOpen(true)}
            >
              <PlusIcon aria-hidden="true" className="size-5" />
            </Button>
            {/* Mode first, model second — the prototype's order, and the order
                of dependence: picking a model can re-resolve the mode. */}
            {composerPill({
              testId: "mobile-composer-mode-pill",
              icon: (
                <ModeIcon
                  aria-hidden="true"
                  className={cn(
                    "text-muted-foreground size-3.5 shrink-0",
                    resolvedMode === "ultra" && "text-[#dabb5e]",
                  )}
                />
              ),
              label: modeLabel,
              ariaLabel: t.inputBox.switchMode(modeLabel),
              className: "shrink-0",
              onClick: () => setModeSheetOpen(true),
            })}
            <ComposerModelPicker
              context={{ ...context, mode: resolvedMode }}
              onContextChange={onContextChange}
              trigger={composerPill({
                testId: "mobile-composer-model-pill",
                icon: (
                  <ModelProviderLogo
                    className="size-3.5 shrink-0"
                    displayName={selectedModel?.display_name}
                    name={[selectedModel?.name, selectedModel?.model]
                      .filter(Boolean)
                      .join(" ")}
                  />
                ),
                label: modelLabel,
                ariaLabel: t.inputBox.switchModel(modelLabel),
              })}
            />
          </PromptInputTools>
          <PromptInputTools>
            <PromptInputSubmit
              className={cn(ROW_BUTTON_CLASS, "rounded-full")}
              disabled={disabled}
              status={status}
              aria-label={
                status === "streaming"
                  ? t.inputBox.stopGenerating
                  : t.inputBox.sendMessage
              }
              data-testid="mobile-composer-send"
            />
          </PromptInputTools>
        </PromptInputFooter>
      </PromptInput>

      <ComposerSheet
        open={sheetOpen}
        onOpenChange={setSheetOpen}
        context={{ ...context, mode: resolvedMode }}
        onContextChange={onContextChange}
        supportThinking={supportThinking}
        supportReasoningEffort={supportReasoningEffort}
        // 文件 reuses the primitive's own hidden input (it already routes
        // through `splitUnsupportedUploadFiles`); the two image entries need
        // their own inputs so the camera can be requested explicitly.
        onPickFile={() => attachments.openFileDialog()}
        onPickPhotoLibrary={() => galleryInputRef.current?.click()}
        onPickCamera={() => cameraInputRef.current?.click()}
      />

      <ComposerModeSheet
        open={modeSheetOpen}
        onOpenChange={setModeSheetOpen}
        context={{ ...context, mode: resolvedMode }}
        onContextChange={onContextChange}
        supportThinking={supportThinking}
      />

      <input
        ref={galleryInputRef}
        type="file"
        accept="image/*"
        multiple
        hidden
        data-testid="mobile-composer-gallery-input"
        onChange={handlePicked}
      />
      <input
        ref={cameraInputRef}
        type="file"
        accept="image/*"
        capture="environment"
        hidden
        data-testid="mobile-composer-camera-input"
        onChange={handlePicked}
      />
    </div>
  );
}
