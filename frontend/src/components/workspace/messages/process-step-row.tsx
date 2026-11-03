"use client";

import {
  BookOpenTextIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  FolderOpenIcon,
  GlobeIcon,
  LightbulbIcon,
  ListTodoIcon,
  Loader2Icon,
  NotebookPenIcon,
  SearchIcon,
  SquareTerminalIcon,
  WrenchIcon,
  XIcon,
} from "lucide-react";
import { useState } from "react";
import type { BundledLanguage } from "shiki";

import { CodeBlock } from "@/components/ai-elements/code-block";
import { useI18n } from "@/core/i18n/hooks";
import type { TimelineStep } from "@/core/messages/timeline";
import { cn } from "@/lib/utils";

import { useArtifacts } from "../artifacts";

import { MarkdownContent } from "./markdown-content";

/**
 * Process step row (docs/plans/2026-11-03-process-timeline-design.md §3, with
 * the user-reviewed interaction refinements):
 *
 * - Collapsed: `[icon] summary ······· [status]` plus a muted preview block of
 *   2–4 lines, differentiated by variant:
 *     - reasoning → first 3 lines of the thinking text (gist);
 *     - narration → first 2 lines;
 *     - toolCall → up to 2 lines of call digest (command / query / path / url
 *       / compact JSON).
 *   The last in-progress row during a live stream shows the *tail* lines
 *   instead (it scrolls with the stream); every settled row keeps its first
 *   lines fixed.
 * - Expanded: well-formatted detail under a left rule; any block longer than
 *   10 lines is clamped to 10 with a "view more (N chars left)" toggle.
 * - Click semantics: the entire row area (icon, summary, preview, padding — a
 *   full-width, ≥44px-tall hit zone) toggles expand/collapse for every
 *   variant; clicks inside the expanded detail never collapse.
 */

const PREVIEW_LINES: Record<TimelineStep["variant"], number> = {
  reasoning: 3,
  narration: 2,
  toolCall: 2,
};

const DETAIL_MAX_LINES = 10;

/** The collapsed preview content: `count` head lines, or tail lines while the row streams. */
function previewLines(text: string, count: number, streamingTail: boolean) {
  const lines = text.split("\n");
  const picked = streamingTail ? lines.slice(-count) : lines.slice(0, count);
  return picked.join("\n").trim();
}

function toolCallDigest(step: TimelineStep) {
  const args = step.args ?? {};
  if (typeof args.command === "string" && args.command.trim()) {
    return args.command;
  }
  for (const key of ["query", "path", "url"]) {
    if (typeof args[key] === "string" && args[key].trim()) {
      return args[key];
    }
  }
  const rest = Object.fromEntries(
    Object.entries(args).filter(([key]) => key !== "description"),
  );
  if (Object.keys(rest).length === 0) {
    return "";
  }
  return JSON.stringify(rest);
}

function useStepSummary(step: TimelineStep) {
  const { t } = useI18n();
  if (step.variant === "reasoning") {
    return t.common.thinking;
  }
  if (step.variant === "narration") {
    return t.processTimeline.narrationLabel;
  }
  const name = step.name ?? "";
  const args = step.args ?? {};
  switch (name) {
    case "web_search":
      return typeof args.query === "string"
        ? t.toolCalls.searchOnWebFor(args.query)
        : t.toolCalls.searchForRelatedInfo;
    case "image_search":
      return typeof args.query === "string"
        ? t.toolCalls.searchForRelatedImagesFor(args.query)
        : t.toolCalls.searchForRelatedImages;
    case "web_fetch":
      return t.toolCalls.viewWebPage;
    case "ls":
      return (
        (typeof args.description === "string" && args.description) ||
        t.toolCalls.listFolder
      );
    case "read_file":
      return (
        (typeof args.description === "string" && args.description) ||
        t.toolCalls.readFile
      );
    case "bash":
      return (
        (typeof args.description === "string" && args.description) ||
        t.toolCalls.executeCommand
      );
    case "write_todos":
      return t.toolCalls.writeTodos;
    default:
      if (ARTIFACT_STEP_NAMES.has(name)) {
        return (
          (typeof args.description === "string" && args.description) ||
          t.toolCalls.writeFile
        );
      }
      return (
        (typeof args.description === "string" && args.description) ||
        t.toolCalls.useTool(name)
      );
  }
}

/** Collapsed preview body per variant (empty string = no preview block). */
function useStepPreview(step: TimelineStep, streamingTail: boolean): string {
  if (step.variant === "reasoning") {
    return previewLines(
      step.reasoning ?? "",
      PREVIEW_LINES.reasoning,
      streamingTail,
    );
  }
  if (step.variant === "narration") {
    return previewLines(
      step.content ?? "",
      PREVIEW_LINES.narration,
      streamingTail,
    );
  }
  const digest = toolCallDigest(step);
  return previewLines(digest, PREVIEW_LINES.toolCall, streamingTail);
}

function stepIcon(step: TimelineStep) {
  if (step.variant === "reasoning") {
    return LightbulbIcon;
  }
  if (step.variant === "narration") {
    return null;
  }
  switch (step.name ?? "") {
    case "web_search":
    case "image_search":
      return SearchIcon;
    case "web_fetch":
      return GlobeIcon;
    case "ls":
      return FolderOpenIcon;
    case "read_file":
      return BookOpenTextIcon;
    case "bash":
      return SquareTerminalIcon;
    case "write_todos":
      return ListTodoIcon;
    default:
      return ARTIFACT_STEP_NAMES.has(step.name ?? "")
        ? NotebookPenIcon
        : WrenchIcon;
  }
}

const ARTIFACT_STEP_NAMES = new Set(["write_file", "str_replace"]);

/** Written-file extensions that render as highlighted code blocks; unknown
 *  extensions fall back to markdown text rendering. */
const EXTENSION_LANGUAGES: Record<string, BundledLanguage> = {
  py: "python",
  js: "javascript",
  mjs: "javascript",
  cjs: "javascript",
  ts: "typescript",
  tsx: "tsx",
  jsx: "jsx",
  json: "json",
  yaml: "yaml",
  yml: "yaml",
  sh: "bash",
  bash: "bash",
  html: "html",
  css: "css",
  sql: "sql",
  go: "go",
  rs: "rust",
  java: "java",
  cpp: "cpp",
  c: "c",
  rb: "ruby",
  php: "php",
};

function StatusSymbol({
  step,
  isLoading,
}: {
  step: TimelineStep;
  isLoading: boolean;
}) {
  if (step.variant !== "toolCall") {
    return null;
  }
  if (step.status === "failed") {
    return <XIcon className="text-destructive size-3.5 shrink-0" />;
  }
  if (step.status === "running" && isLoading) {
    return (
      <Loader2Icon className="text-muted-foreground size-3.5 shrink-0 animate-spin" />
    );
  }
  return (
    <span className="text-muted-foreground shrink-0 font-mono text-xs">✓</span>
  );
}

/**
 * Caps a long block to `DETAIL_MAX_LINES` newline-delimited lines with a
 * "view more (N chars left)" toggle; clicking again re-collapses the block.
 * Blocks within the cap render as-is with no toggle.
 */
function ClampedBlock({
  text,
  render,
  className,
}: {
  text: string;
  render: (text: string) => React.ReactNode;
  className?: string;
}) {
  const { t } = useI18n();
  const [fullyExpanded, setFullyExpanded] = useState(false);
  const lines = text.split("\n");
  const overflowing = lines.length > DETAIL_MAX_LINES;
  const shown =
    overflowing && !fullyExpanded
      ? lines.slice(0, DETAIL_MAX_LINES).join("\n")
      : text;
  const remainingChars = overflowing
    ? text.trimStart().length - shown.trimStart().length
    : 0;

  return (
    <div className={className}>
      {render(shown)}
      {overflowing && (
        <button
          type="button"
          data-testid={
            fullyExpanded
              ? "process-step-clamp-less"
              : "process-step-clamp-more"
          }
          className="text-muted-foreground hover:text-foreground mt-1 cursor-pointer text-xs"
          onClick={(event) => {
            event.stopPropagation();
            setFullyExpanded(!fullyExpanded);
          }}
        >
          {fullyExpanded
            ? t.processTimeline.viewLess
            : t.processTimeline.viewMore(remainingChars)}
        </button>
      )}
    </div>
  );
}

function StepDetail({
  step,
  isLoading,
}: {
  step: TimelineStep;
  isLoading: boolean;
}) {
  const { t } = useI18n();
  const { setOpen, select } = useArtifacts();

  if (step.variant === "reasoning") {
    return (
      <ClampedBlock
        text={step.reasoning ?? ""}
        render={(text) => (
          <MarkdownContent content={text} isLoading={isLoading} />
        )}
      />
    );
  }
  if (step.variant === "narration") {
    return (
      <ClampedBlock
        text={step.content ?? ""}
        render={(text) => (
          <MarkdownContent content={text} isLoading={isLoading} />
        )}
      />
    );
  }

  const args = step.args ?? {};
  const command = typeof args.command === "string" ? args.command : undefined;
  const isArtifactStep = ARTIFACT_STEP_NAMES.has(step.name ?? "");
  const artifactUrl =
    isArtifactStep && step.messageId && typeof args.path === "string"
      ? new URL(
          `write-file:${args.path}?message_id=${step.messageId}&tool_call_id=${step.id}`,
        ).toString()
      : null;
  // write_file / str_replace: render the written file content itself (markdown
  // for .md, a code block otherwise) instead of the raw JSON arguments.
  const artifactContent =
    artifactUrl && typeof args.content === "string" ? args.content : "";
  const argsJson =
    !isArtifactStep &&
    Object.keys(args).filter((key) => key !== "description").length > 0
      ? JSON.stringify(
          Object.fromEntries(
            Object.entries(args).filter(([key]) => key !== "description"),
          ),
          null,
          2,
        )
      : "";

  return (
    <div className="flex min-w-0 flex-col gap-2 text-sm">
      {artifactUrl && (
        <button
          type="button"
          data-testid="process-step-open-artifact"
          className="text-primary w-fit cursor-pointer text-xs hover:underline"
          onClick={(event) => {
            event.stopPropagation();
            select(artifactUrl);
            setOpen(true);
          }}
        >
          {t.processTimeline.openArtifact}
          {typeof args.path === "string" ? `: ${args.path}` : ""}
        </button>
      )}
      {artifactContent && (
        <ClampedBlock
          text={artifactContent}
          render={(text) => {
            const path = typeof args.path === "string" ? args.path : "";
            const ext = path.split(".").pop() ?? "";
            const language = EXTENSION_LANGUAGES[ext];
            // Markdown renders as formatted content; files with an unknown
            // extension fall back to plain markdown text as well.
            if (!language) {
              return <MarkdownContent content={text} isLoading={false} />;
            }
            return (
              <CodeBlock
                className="mx-0 max-w-full border-none px-0"
                showLineNumbers={false}
                language={language}
                code={text}
              />
            );
          }}
        />
      )}
      {command ? (
        <ClampedBlock
          text={command}
          render={(text) => (
            <CodeBlock
              className="mx-0 border-none px-0"
              showLineNumbers={false}
              language="bash"
              code={text}
            />
          )}
        />
      ) : (
        argsJson && (
          <ClampedBlock
            text={argsJson}
            render={(text) => (
              <CodeBlock
                className="mx-0 max-w-full border-none px-0"
                showLineNumbers={false}
                language="json"
                code={text}
              />
            )}
          />
        )
      )}
      {step.status === "failed" && step.resultText == null && (
        <div className="text-destructive text-sm">{t.subtasks.failed}</div>
      )}
      {step.resultText != null && <ToolResultContent step={step} />}
    </div>
  );
}

function ToolResultContent({ step }: { step: TimelineStep }) {
  // web_search keeps the link-list rendering; image_search a thumbnail grid.
  if (step.name === "web_search" && Array.isArray(step.result)) {
    return (
      <ul className="flex flex-col gap-1">
        {(step.result as { url: string; title: string }[]).map((item) => (
          <li key={item.url}>
            <a
              className="text-primary hover:underline"
              href={item.url}
              target="_blank"
              rel="noopener noreferrer"
            >
              {item.title}
            </a>
          </li>
        ))}
      </ul>
    );
  }
  if (
    step.name === "image_search" &&
    step.result &&
    typeof step.result === "object" &&
    "results" in step.result &&
    Array.isArray(step.result.results)
  ) {
    return (
      <div className="flex flex-wrap gap-2">
        {step.result.results.map(
          (item: {
            image_url: string;
            thumbnail_url: string;
            title: string;
            source_url: string;
          }) => (
            <a
              key={item.image_url}
              className="bg-accent size-24 overflow-hidden rounded-lg"
              href={item.source_url}
              target="_blank"
              rel="noopener noreferrer"
            >
              <img
                className="size-full object-cover"
                src={item.thumbnail_url}
                alt={item.title}
                width={96}
                height={96}
              />
            </a>
          ),
        )}
      </div>
    );
  }
  if (typeof step.result === "string") {
    return (
      <ClampedBlock
        text={step.result}
        render={(text) => <MarkdownContent content={text} isLoading={false} />}
      />
    );
  }
  return (
    <ClampedBlock
      text={JSON.stringify(step.result ?? null, null, 2) ?? ""}
      render={(text) => (
        <CodeBlock
          className="mx-0 max-w-full border-none px-0"
          showLineNumbers={false}
          language="json"
          code={text}
        />
      )}
    />
  );
}

export function ProcessStepRow({
  step,
  roundIndex,
  isLoading = false,
  failed = false,
  streamingTail = false,
}: {
  step: TimelineStep;
  roundIndex?: number;
  isLoading?: boolean;
  /** D7: failed steps open with their error detail visible until collapsed. */
  failed?: boolean;
  /**
   * The last in-progress row of the live stream: its collapsed preview shows
   * the tail lines (scrolling with the stream) instead of the head lines.
   */
  streamingTail?: boolean;
}) {
  // Row-local state is safe: step keys are stable (tool_call/message ids), so
  // streaming re-renders reuse the mounted row instead of resetting it.
  const { t } = useI18n();
  const [userPreference, setUserPreference] = useState<boolean | null>(null);
  // D2 (revised by user testing): nothing auto-expands; failed steps (D7)
  // still open by default. Manual clicks toggle any row.
  const expanded = userPreference ?? failed;
  const summary = useStepSummary(step);
  const preview = useStepPreview(step, streamingTail && !expanded);
  const Icon = stepIcon(step);
  const hasDetail =
    step.variant === "toolCall" ||
    Boolean(step.reasoning) ||
    Boolean(step.content);

  const toggle = () => {
    if (hasDetail) {
      setUserPreference(!expanded);
    }
  };

  return (
    <div
      className="w-full"
      data-testid="process-step-row"
      data-step-variant={step.variant}
      data-step-status={step.status}
    >
      {/* The entire row — icon, summary, preview, padding — is the toggle hit
          zone (full width, ≥44px tall for touch). */}
      <button
        type="button"
        aria-expanded={expanded}
        data-testid="process-step-toggle"
        className={cn(
          "flex min-h-11 w-full cursor-pointer flex-col gap-0.5 rounded px-1 py-2 text-left text-sm",
          "hover:bg-accent/50",
        )}
        onClick={toggle}
      >
        <div className="flex min-h-7 w-full items-center gap-2">
          {Icon ? (
            <Icon className="text-muted-foreground size-4 shrink-0" />
          ) : (
            <span className="size-4 shrink-0" />
          )}
          <span
            className={cn(
              "min-w-0 flex-1 truncate",
              step.variant === "narration" && "text-muted-foreground",
              step.status === "failed" && "text-destructive",
            )}
          >
            {summary}
          </span>
          {roundIndex != null && roundIndex > 0 && (
            <span className="text-muted-foreground/70 shrink-0 font-mono text-[11px]">
              #{roundIndex}
            </span>
          )}
          <StatusSymbol step={step} isLoading={isLoading} />
          {hasDetail &&
            (expanded ? (
              <ChevronDownIcon className="text-muted-foreground size-3.5 shrink-0" />
            ) : (
              <ChevronRightIcon className="text-muted-foreground size-3.5 shrink-0" />
            ))}
        </div>
        {!expanded && preview && (
          <span
            data-testid="process-step-preview"
            className="text-muted-foreground/80 line-clamp-4 w-full text-xs whitespace-pre-line"
          >
            {preview}
          </span>
        )}
      </button>
      {expanded && hasDetail && (
        <div
          data-testid="process-step-detail"
          className="border-border/60 ml-6 border-l-2 pl-3"
        >
          {/* Long details scroll the summary row out of view; keep a collapse
              affordance at the top of the detail so the compact state is
              always one visible click away (the row itself also toggles). */}
          <button
            type="button"
            data-testid="process-step-collapse"
            className="text-muted-foreground hover:text-foreground -my-1 flex min-h-11 w-full cursor-pointer items-center gap-1.5 py-2 text-left text-xs"
            onClick={toggle}
          >
            <ChevronDownIcon className="size-3.5 shrink-0 rotate-180" />
            {t.processTimeline.viewLess}
          </button>
          <StepDetail step={step} isLoading={isLoading} />
        </div>
      )}
    </div>
  );
}

export { ARTIFACT_STEP_NAMES as PROCESS_TIMELINE_ARTIFACT_STEP_NAMES };
