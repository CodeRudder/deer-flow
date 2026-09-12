"use client";

import type { ChatStatus } from "ai";
import { PlusIcon, SparklesIcon, XIcon } from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
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
import { useSkills } from "@/core/skills/hooks";
import type { AgentThreadContext } from "@/core/threads";
import { splitUnsupportedUploadFiles } from "@/core/uploads";
import { cn } from "@/lib/utils";

import { resolveMode, type ComposerMode } from "./composer-mode";
import { ComposerSheet } from "./composer-sheet";

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

type MobileComposerProps = {
  status?: ChatStatus;
  context: ComposerContext;
  disabled?: boolean;
  /** Shown instead of the placeholder while `disabled` (clarification pending). */
  disabledPlaceholder?: string;
  onContextChange: (context: ComposerContext) => void;
  onSubmit: (message: PromptInputMessage) => void | Promise<void>;
  onStop: () => void;
};

/**
 * Mobile composer (prototype ②): the main row is only ＋ / input / send.
 *
 * It is built from the same `PromptInput` primitives the desktop `InputBox`
 * uses — the attachment pipeline, the Enter-to-send handling and the
 * submit/stop button all come from `@/components/ai-elements/prompt-input` —
 * but none of the desktop's controls are rendered here. The model picker (a
 * header trigger on the desktop, next to the send button here) and the whole
 * footer row (mode, image/video generation models, reasoning effort) live in
 * the `＋` sheet (`composer-sheet.tsx`), because six controls cannot share one
 * row and still hold the 44px touch floor at 390px.
 *
 * Requires a `PromptInputProvider` above it, exactly like the desktop page:
 * `useSpecificChatMode()` (called by `useChatPage()`) reads the prompt-input
 * controller, and the `＋` sheet's pickers feed the same attachment state.
 */
export function MobileComposer({
  status = "ready",
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
  const galleryInputRef = useRef<HTMLInputElement | null>(null);
  const cameraInputRef = useRef<HTMLInputElement | null>(null);

  const selectedModel = useMemo(
    () =>
      models.find((model) => model.name === context.model_name) ?? models[0],
    [context.model_name, models],
  );
  const supportThinking = selectedModel?.supports_thinking ?? false;
  const supportReasoningEffort =
    selectedModel?.supports_reasoning_effort ?? false;
  const resolvedMode = resolveMode(context.mode, supportThinking);

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
      // No bottom safe-area inset here on purpose: the tab bar below the
      // composer already carries it on this route (`app/m/layout.tsx` renders
      // the two as siblings), and adding it twice would leave a dead gap above
      // the home indicator.
      className="bg-background/95 shrink-0 border-t px-2 pt-1.5 pb-1.5 supports-backdrop-filter:backdrop-blur"
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
          <PromptInputTools>
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
        context={{ ...context, mode: context.mode ?? resolvedMode }}
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
