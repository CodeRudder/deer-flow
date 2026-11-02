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

import { CodeBlock } from "@/components/ai-elements/code-block";
import { useI18n } from "@/core/i18n/hooks";
import type { TimelineStep } from "@/core/messages/timeline";
import { cn } from "@/lib/utils";

import { useArtifacts } from "../artifacts";

import { MarkdownContent } from "./markdown-content";

/**
 * Single-line process step row (docs/plans/2026-11-03-process-timeline-design.md §3).
 *
 * Collapsed: `[icon] summary ··············· [status]` — no border, no card.
 * Expanded: detail under a left rule, indented one level — never a big card.
 */

function firstLine(text: string, max = 60) {
  const line =
    text
      .split("\n")
      .find((part) => part.trim().length > 0)
      ?.trim() ?? "";
  return line.length > max ? `${line.slice(0, max)}…` : line;
}

function useStepSummary(step: TimelineStep) {
  const { t } = useI18n();
  if (step.variant === "reasoning") {
    return firstLine(step.reasoning ?? "") || t.common.thinking;
  }
  if (step.variant === "narration") {
    return firstLine(step.content ?? "");
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
      if (ARTIFACT_STEP_NAMES_SET.has(name)) {
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

const ARTIFACT_STEP_NAMES_SET = new Set(["write_file", "str_replace"]);

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
      return ARTIFACT_STEP_NAMES_SET.has(step.name ?? "")
        ? NotebookPenIcon
        : WrenchIcon;
  }
}

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

function StepDetail({
  step,
  isLoading,
}: {
  step: TimelineStep;
  isLoading: boolean;
}) {
  const { t } = useI18n();
  if (step.variant === "reasoning") {
    return (
      <MarkdownContent content={step.reasoning ?? ""} isLoading={isLoading} />
    );
  }
  if (step.variant === "narration") {
    return (
      <MarkdownContent content={step.content ?? ""} isLoading={isLoading} />
    );
  }

  const args = step.args ?? {};
  const command = typeof args.command === "string" ? args.command : undefined;
  return (
    <div className="flex min-w-0 flex-col gap-2 text-sm">
      {command ? (
        <CodeBlock
          className="mx-0 border-none px-0"
          showLineNumbers={false}
          language="bash"
          code={command}
        />
      ) : (
        Object.keys(args).filter((key) => key !== "description").length > 0 && (
          <CodeBlock
            className="mx-0 max-w-full border-none px-0"
            showLineNumbers={false}
            language="json"
            code={JSON.stringify(
              Object.fromEntries(
                Object.entries(args).filter(([key]) => key !== "description"),
              ),
              null,
              2,
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
              {}
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
    return <MarkdownContent content={step.result} isLoading={false} />;
  }
  return (
    <CodeBlock
      className="mx-0 max-w-full border-none px-0"
      showLineNumbers={false}
      language="json"
      code={JSON.stringify(step.result, null, 2)}
    />
  );
}

export function ProcessStepRow({
  step,
  roundIndex,
  isLoading = false,
  forceExpanded = false,
  failed = false,
}: {
  step: TimelineStep;
  roundIndex?: number;
  isLoading?: boolean;
  /** D2: the streaming newest reasoning row is expanded by the container. */
  forceExpanded?: boolean;
  /** D7: failed steps open with their error detail visible until collapsed. */
  failed?: boolean;
}) {
  // Row-local state is safe: step keys are stable (tool_call/message ids), so
  // streaming re-renders reuse the mounted row instead of resetting it.
  const [userPreference, setUserPreference] = useState<boolean | null>(null);
  const expanded = forceExpanded || (userPreference ?? failed);
  const summary = useStepSummary(step);
  const Icon = stepIcon(step);
  const { setOpen, select } = useArtifacts();
  const isArtifactStep = step.opensArtifact === true;
  const hasDetail =
    step.variant === "toolCall" ||
    Boolean(step.reasoning) ||
    Boolean(step.content);

  const toggle = () => {
    if (hasDetail) {
      setUserPreference(!expanded);
    }
  };

  const handleRowClick = () => {
    if (
      isArtifactStep &&
      step.messageId &&
      typeof step.args?.path === "string"
    ) {
      // Artifact rows keep their link semantics: the row opens the file panel,
      // the trailing chevron zone toggles the detail (§3.2).
      select(
        new URL(
          `write-file:${step.args.path}?message_id=${step.messageId}&tool_call_id=${step.id}`,
        ).toString(),
      );
      setOpen(true);
      return;
    }
    toggle();
  };

  return (
    <div
      className="w-full"
      data-testid="process-step-row"
      data-step-variant={step.variant}
      data-step-status={step.status}
    >
      <div
        className={cn(
          "group flex min-h-7 w-full items-center gap-2 rounded px-1 text-sm",
          "hover:bg-accent/50",
        )}
      >
        <button
          type="button"
          aria-expanded={expanded}
          data-testid="process-step-toggle"
          className={cn(
            "flex min-w-0 flex-1 items-center gap-2 py-1 text-left",
            isArtifactStep && "cursor-pointer",
          )}
          onClick={handleRowClick}
        >
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
        </button>
        {roundIndex != null && roundIndex > 0 && (
          <span className="text-muted-foreground/70 shrink-0 font-mono text-[11px]">
            #{roundIndex}
          </span>
        )}
        <StatusSymbol step={step} isLoading={isLoading} />
        {hasDetail && (
          <button
            type="button"
            aria-expanded={expanded}
            aria-label="toggle step detail"
            data-testid="process-step-chevron"
            className="text-muted-foreground hover:text-foreground shrink-0 rounded p-0.5"
            onClick={toggle}
          >
            {expanded ? (
              <ChevronDownIcon className="size-3.5" />
            ) : (
              <ChevronRightIcon className="size-3.5" />
            )}
          </button>
        )}
      </div>
      {expanded && hasDetail && (
        <div
          data-testid="process-step-detail"
          className="border-border/60 ml-6 border-l-2 pl-3"
        >
          <StepDetail step={step} isLoading={isLoading} />
        </div>
      )}
    </div>
  );
}

export { ARTIFACT_STEP_NAMES_SET as PROCESS_TIMELINE_ARTIFACT_STEP_NAMES };
