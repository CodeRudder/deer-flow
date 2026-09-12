import type { Message } from "@langchain/langgraph-sdk";
import {
  BookOpenTextIcon,
  ChevronUp,
  CoinsIcon,
  FolderOpenIcon,
  GlobeIcon,
  LightbulbIcon,
  ListTodoIcon,
  NotebookPenIcon,
  SearchIcon,
  SquareTerminalIcon,
  WrenchIcon,
} from "lucide-react";
import { useMemo, useState } from "react";

import {
  ChainOfThought,
  ChainOfThoughtContent,
  ChainOfThoughtSearchResult,
  ChainOfThoughtSearchResults,
  ChainOfThoughtStep,
} from "@/components/ai-elements/chain-of-thought";
import { CodeBlock } from "@/components/ai-elements/code-block";
import { Button } from "@/components/ui/button";
import { useI18n } from "@/core/i18n/hooks";
import { formatTokenCount } from "@/core/messages/usage";
import type { TokenDebugStep } from "@/core/messages/usage-model";
import {
  extractContentFromMessage,
  extractReasoningContentFromMessage,
  findToolCallResult,
} from "@/core/messages/utils";
import { useRehypeSplitWordsIntoSpans } from "@/core/rehype";
import { extractTitleFromMarkdown } from "@/core/utils/markdown";
import { env } from "@/env";
import { cn } from "@/lib/utils";

import { useArtifacts } from "../artifacts";
import { FlipDisplay } from "../flip-display";
import { Tooltip } from "../tooltip";

import { MarkdownContent } from "./markdown-content";

export function MessageGroup({
  className,
  messages,
  isLoading = false,
  tokenDebugSteps = [],
  showTokenDebugSummaries = false,
  collapsedSteps = false,
}: {
  className?: string;
  messages: Message[];
  isLoading?: boolean;
  tokenDebugSteps?: TokenDebugStep[];
  showTokenDebugSummaries?: boolean;
  /**
   * Mobile-only (T13 / `FEATURE_LIST.md` C6): render the tool-call process as a
   * single "ran N steps · M tools" row that expands into the usual detail,
   * instead of starting with the panel open. The phone transcript has the
   * narrow column to spare and the dense panel is what a 390px screen cannot
   * afford; the desktop keeps the panel exactly as it was, because the default
   * is `false` and the false branch is the original JSX, untouched.
   */
  collapsedSteps?: boolean;
}) {
  const { t } = useI18n();
  const [showAbove, setShowAbove] = useState(
    env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true",
  );
  const [showLastThinking, setShowLastThinking] = useState(
    env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true",
  );
  const steps = useMemo(() => convertToSteps(messages), [messages]);
  const debugStepByMessageId = useMemo(
    () =>
      new Map(
        tokenDebugSteps.map(
          (step) => [step.messageId || step.id, step] as const,
        ),
      ),
    [tokenDebugSteps],
  );
  const toolCallCountByMessageId = useMemo(() => {
    const counts = new Map<string, number>();

    for (const step of steps) {
      if (step.type !== "toolCall" || !step.messageId) {
        continue;
      }

      counts.set(step.messageId, (counts.get(step.messageId) ?? 0) + 1);
    }

    return counts;
  }, [steps]);
  const lastToolCallStep = useMemo(() => {
    const filteredSteps = steps.filter((step) => step.type === "toolCall");
    return filteredSteps[filteredSteps.length - 1];
  }, [steps]);
  const aboveLastToolCallSteps = useMemo(() => {
    if (lastToolCallStep) {
      const index = steps.indexOf(lastToolCallStep);
      return steps.slice(0, index);
    }
    return [];
  }, [lastToolCallStep, steps]);
  const afterLastToolCallAssistantTextSteps = useMemo(() => {
    if (!lastToolCallStep) {
      return [];
    }
    const index = steps.indexOf(lastToolCallStep);
    return steps
      .slice(index + 1)
      .filter((step) => step.type === "assistantText");
  }, [lastToolCallStep, steps]);
  const lastReasoningStep = useMemo(() => {
    if (lastToolCallStep) {
      const index = steps.indexOf(lastToolCallStep);
      return steps.slice(index + 1).find((step) => step.type === "reasoning");
    } else {
      const filteredSteps = steps.filter((step) => step.type === "reasoning");
      return filteredSteps[filteredSteps.length - 1];
    }
  }, [lastToolCallStep, steps]);
  const rehypePlugins = useRehypeSplitWordsIntoSpans(isLoading);
  const firstEligibleDebugSummaryStepIndexByMessageId = useMemo(() => {
    const firstIndices = new Map<string, number>();

    if (!showTokenDebugSummaries) {
      return firstIndices;
    }

    for (const [index, step] of steps.entries()) {
      const messageId = step.messageId;
      if (!messageId || firstIndices.has(messageId)) {
        continue;
      }

      const debugStep = debugStepByMessageId.get(messageId);
      if (!debugStep) {
        continue;
      }

      const toolCallCount = toolCallCountByMessageId.get(messageId) ?? 0;
      if (!debugStep.sharedAttribution && toolCallCount > 0) {
        continue;
      }
      if (
        !debugStep.sharedAttribution &&
        toolCallCount === 0 &&
        debugStep.label === t.common.thinking &&
        debugStep.secondaryLabels.length === 0
      ) {
        continue;
      }

      firstIndices.set(messageId, index);
    }

    return firstIndices;
  }, [
    debugStepByMessageId,
    showTokenDebugSummaries,
    steps,
    t.common.thinking,
    toolCallCountByMessageId,
  ]);

  const renderDebugSummary = (
    messageId: string | undefined,
    stepIndex: number,
  ) => {
    if (!showTokenDebugSummaries || !messageId) {
      return null;
    }

    const debugStep = debugStepByMessageId.get(messageId);
    if (!debugStep) {
      return null;
    }
    if (
      firstEligibleDebugSummaryStepIndexByMessageId.get(messageId) !== stepIndex
    ) {
      return null;
    }

    return (
      <ChainOfThoughtStep
        key={`token-debug-${messageId}`}
        icon={CoinsIcon}
        label={
          <DebugStepLabel
            label={debugStep.label}
            token={formatDebugToken(debugStep, t)}
          />
        }
        description={
          debugStep.sharedAttribution
            ? t.tokenUsage.sharedAttribution
            : undefined
        }
      >
        {debugStep.secondaryLabels.length > 0 && (
          <ChainOfThoughtSearchResults>
            {debugStep.secondaryLabels.map((label, index) => (
              <ChainOfThoughtSearchResult
                key={`${debugStep.id}-${index}-${label}`}
              >
                {label}
              </ChainOfThoughtSearchResult>
            ))}
          </ChainOfThoughtSearchResults>
        )}
      </ChainOfThoughtStep>
    );
  };

  const renderToolCall = (
    step: CoTToolCallStep,
    options?: { isLast?: boolean },
  ) => {
    const debugStep =
      showTokenDebugSummaries && step.messageId
        ? debugStepByMessageId.get(step.messageId)
        : undefined;

    return (
      <ToolCall
        key={step.id}
        {...step}
        isLast={options?.isLast}
        isLoading={isLoading}
        tokenDebugStep={
          debugStep && !debugStep.sharedAttribution ? debugStep : undefined
        }
      />
    );
  };

  const renderStep = (step: CoTStep) => {
    const stepIndex = steps.indexOf(step);
    if (step.type === "reasoning") {
      return [
        renderDebugSummary(step.messageId, stepIndex),
        <ChainOfThoughtStep
          key={step.id}
          label={
            <MarkdownContent
              content={step.reasoning ?? ""}
              isLoading={isLoading}
              rehypePlugins={rehypePlugins}
            />
          }
        ></ChainOfThoughtStep>,
      ];
    }
    if (step.type === "assistantText") {
      return [
        renderDebugSummary(step.messageId, stepIndex),
        <ChainOfThoughtStep
          key={step.id}
          label={
            <MarkdownContent
              content={step.content}
              isLoading={isLoading}
              rehypePlugins={rehypePlugins}
            />
          }
        ></ChainOfThoughtStep>,
      ];
    }
    return [
      renderDebugSummary(step.messageId, stepIndex),
      renderToolCall(step),
    ];
  };

  const lastReasoningDebugStep =
    showTokenDebugSummaries && lastReasoningStep?.messageId
      ? debugStepByMessageId.get(lastReasoningStep.messageId)
      : undefined;

  // All steps filtered out (e.g. a clarification-only message) — no panel.
  if (steps.length === 0) {
    return null;
  }

  // Mobile: summarise first, detail on tap. Reasoning-only groups have no
  // tool call to summarise, so they keep the panel below rather than reading
  // "ran 0 steps · 0 tools".
  if (collapsedSteps && steps.some((step) => step.type === "toolCall")) {
    return (
      <CollapsedSteps
        className={className}
        renderStep={renderStep}
        steps={steps}
      />
    );
  }

  return (
    <ChainOfThought
      className={cn("w-full gap-2 rounded-lg border p-0.5", className)}
      open={true}
    >
      {aboveLastToolCallSteps.length > 0 && (
        <Button
          key="above"
          className="w-full items-start justify-start text-left"
          variant="ghost"
          onClick={() => setShowAbove(!showAbove)}
        >
          <ChainOfThoughtStep
            label={
              <span className="opacity-60">
                {showAbove
                  ? t.toolCalls.lessSteps
                  : t.toolCalls.moreSteps(aboveLastToolCallSteps.length)}
              </span>
            }
            icon={
              <ChevronUp
                className={cn(
                  "size-4 opacity-60 transition-transform duration-200",
                  showAbove ? "rotate-180" : "",
                )}
              />
            }
          ></ChainOfThoughtStep>
        </Button>
      )}
      {(lastToolCallStep ??
        steps.some((step) => step.type === "assistantText")) && (
        <ChainOfThoughtContent className="px-4 pb-2">
          {(lastToolCallStep
            ? showAbove
              ? aboveLastToolCallSteps
              : aboveLastToolCallSteps.filter(
                  (step) => step.type === "assistantText",
                )
            : steps.filter((step) => step.type === "assistantText")
          ).flatMap(renderStep)}
          {lastToolCallStep && (
            <>
              {renderDebugSummary(
                lastToolCallStep.messageId,
                steps.indexOf(lastToolCallStep),
              )}
              <FlipDisplay uniqueKey={lastToolCallStep.id ?? ""}>
                {renderToolCall(lastToolCallStep, { isLast: true })}
              </FlipDisplay>
              {afterLastToolCallAssistantTextSteps.flatMap(renderStep)}
            </>
          )}
        </ChainOfThoughtContent>
      )}
      {lastReasoningStep && (
        <>
          {renderDebugSummary(
            lastReasoningStep.messageId,
            steps.indexOf(lastReasoningStep),
          )}
          <Button
            key={lastReasoningStep.id}
            className="w-full items-start justify-start text-left"
            variant="ghost"
            onClick={() => setShowLastThinking(!showLastThinking)}
          >
            <div className="flex w-full items-center justify-between">
              <ChainOfThoughtStep
                className="font-normal"
                label={
                  <DebugStepLabel
                    label={t.common.thinking}
                    token={shouldInlineThinkingToken({
                      debugStep: lastReasoningDebugStep,
                      toolCallCount: lastReasoningStep.messageId
                        ? (toolCallCountByMessageId.get(
                            lastReasoningStep.messageId,
                          ) ?? 0)
                        : 0,
                      enabled: showTokenDebugSummaries,
                      thinkingLabel: t.common.thinking,
                      t,
                    })}
                  />
                }
                icon={LightbulbIcon}
              ></ChainOfThoughtStep>
              <div>
                <ChevronUp
                  className={cn(
                    "text-muted-foreground size-4",
                    showLastThinking ? "" : "rotate-180",
                  )}
                />
              </div>
            </div>
          </Button>
          {showLastThinking && (
            <ChainOfThoughtContent className="px-4 pb-2">
              <ChainOfThoughtStep
                key={lastReasoningStep.id}
                label={
                  <MarkdownContent
                    content={lastReasoningStep.reasoning ?? ""}
                    isLoading={isLoading}
                    rehypePlugins={rehypePlugins}
                  />
                }
              ></ChainOfThoughtStep>
            </ChainOfThoughtContent>
          )}
        </>
      )}
    </ChainOfThought>
  );
}

/**
 * Steps whose row is a link rather than a log line: `ToolCall` turns a
 * `write_file` / `str_replace` step into the mobile artifact screen's entry
 * point (`select()` + `setOpen(true)` → the page navigates). On the phone that
 * row is the *only* route to a draft — a draft exists only in the tool call's
 * arguments, so it is not in `thread.values.artifacts` and the header's "⋯"
 * menu cannot offer it.
 */
const ARTIFACT_STEP_NAMES = new Set(["write_file", "str_replace"]);

/**
 * Mobile rendering of a step group (T13 / C6, prototype ②'s `.disc` block):
 * one row — "ran 3 steps · 2 tools" — that expands into the very same steps
 * the desktop panel paints. The row is a 44px tap target, and the count is the
 * number of rows the expansion holds (`steps`), so the summary and the detail
 * can never disagree.
 *
 * A group whose steps open an artifact starts expanded: collapsing it would
 * hide the entry point to the draft screen behind an extra tap, which is a
 * behaviour the transcript had before this summary existed (T6 / F7-3). The
 * summary still folds everything on a run that only reads and searches.
 *
 * This is only reached when `MessageGroup` is given `collapsedSteps`, which no
 * desktop caller passes.
 */
function CollapsedSteps({
  className,
  renderStep,
  steps,
}: {
  className?: string;
  renderStep: (step: CoTStep) => React.ReactNode[];
  steps: CoTStep[];
}) {
  const { t } = useI18n();
  const [open, setOpen] = useState(() =>
    steps.some(
      (step) => step.type === "toolCall" && ARTIFACT_STEP_NAMES.has(step.name),
    ),
  );
  const toolCallCount = steps.filter((step) => step.type === "toolCall").length;

  return (
    <ChainOfThought
      className={cn("w-full gap-2 rounded-lg border p-0.5", className)}
      open={open}
      onOpenChange={setOpen}
    >
      <Button
        aria-expanded={open}
        className="min-h-11 w-full items-start justify-start text-left"
        data-testid="mobile-collapsed-steps"
        variant="ghost"
        onClick={() => setOpen(!open)}
      >
        <div className="flex w-full items-center justify-between">
          <ChainOfThoughtStep
            className="font-normal"
            icon={WrenchIcon}
            label={
              <span className="opacity-60">
                {t.toolCalls.executedSteps(steps.length)}
                {" · "}
                {t.toolCalls.toolsUsed(toolCallCount)}
              </span>
            }
          />
          <ChevronUp
            className={cn(
              "text-muted-foreground size-4 shrink-0",
              open ? "" : "rotate-180",
            )}
          />
        </div>
      </Button>
      {open && (
        <ChainOfThoughtContent className="px-4 pb-2">
          {steps.flatMap(renderStep)}
        </ChainOfThoughtContent>
      )}
    </ChainOfThought>
  );
}

function formatDebugToken(
  debugStep: TokenDebugStep,
  t: ReturnType<typeof useI18n>["t"],
) {
  return debugStep.usage
    ? `${formatTokenCount(debugStep.usage.totalTokens)} ${t.tokenUsage.label}`
    : t.tokenUsage.unavailableShort;
}

function shouldInlineThinkingToken({
  debugStep,
  toolCallCount,
  enabled,
  thinkingLabel,
  t,
}: {
  debugStep?: TokenDebugStep;
  toolCallCount: number;
  enabled: boolean;
  thinkingLabel: string;
  t: ReturnType<typeof useI18n>["t"];
}) {
  if (
    !enabled ||
    !debugStep ||
    debugStep.sharedAttribution ||
    toolCallCount > 0 ||
    debugStep.label !== thinkingLabel
  ) {
    return null;
  }

  return formatDebugToken(debugStep, t);
}

function DebugStepLabel({
  label,
  token,
}: {
  label: React.ReactNode;
  token?: string | null;
}) {
  return (
    <div className="flex items-center justify-between gap-3">
      <div className="min-w-0 flex-1">{label}</div>
      {token ? (
        <div className="text-muted-foreground shrink-0 font-mono text-[11px]">
          {token}
        </div>
      ) : null}
    </div>
  );
}

function ToolCall({
  id,
  messageId,
  name,
  args,
  result,
  isLast = false,
  isLoading = false,
  tokenDebugStep,
}: {
  id?: string;
  messageId?: string;
  name: string;
  args: Record<string, unknown>;
  result?: string | Record<string, unknown>;
  isLast?: boolean;
  isLoading?: boolean;
  tokenDebugStep?: TokenDebugStep;
}) {
  const { t } = useI18n();
  const { setOpen, autoOpen, autoSelect, selectedArtifact, select } =
    useArtifacts();
  const tokenLabel = tokenDebugStep
    ? formatDebugToken(tokenDebugStep, t)
    : null;
  const resolveLabel = (fallback: React.ReactNode) =>
    tokenDebugStep ? (
      <DebugStepLabel label={tokenDebugStep.label} token={tokenLabel} />
    ) : (
      fallback
    );

  if (name === "web_search") {
    let label: React.ReactNode = t.toolCalls.searchForRelatedInfo;
    if (typeof args.query === "string") {
      label = t.toolCalls.searchOnWebFor(args.query);
    }
    return (
      <ChainOfThoughtStep
        key={id}
        label={resolveLabel(label)}
        icon={SearchIcon}
      >
        {Array.isArray(result) && (
          <ChainOfThoughtSearchResults>
            {result.map((item) => (
              <ChainOfThoughtSearchResult key={item.url}>
                <a href={item.url} target="_blank" rel="noopener noreferrer">
                  {item.title}
                </a>
              </ChainOfThoughtSearchResult>
            ))}
          </ChainOfThoughtSearchResults>
        )}
      </ChainOfThoughtStep>
    );
  } else if (name === "image_search") {
    let label: React.ReactNode = t.toolCalls.searchForRelatedImages;
    if (typeof args.query === "string") {
      label = t.toolCalls.searchForRelatedImagesFor(args.query);
    }
    const results = (
      result as {
        results: {
          source_url: string;
          thumbnail_url: string;
          image_url: string;
          title: string;
        }[];
      }
    )?.results;
    return (
      <ChainOfThoughtStep
        key={id}
        label={resolveLabel(label)}
        icon={SearchIcon}
      >
        {Array.isArray(results) && (
          <ChainOfThoughtSearchResults>
            {Array.isArray(results) &&
              results.map((item) => (
                <Tooltip key={item.image_url} content={item.title}>
                  <a
                    className="size-24 overflow-hidden rounded-lg object-cover"
                    href={item.source_url}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    <div className="bg-accent size-24">
                      <img
                        className="size-full object-cover"
                        src={item.thumbnail_url}
                        alt={item.title}
                        width={100}
                        height={100}
                      />
                    </div>
                  </a>
                </Tooltip>
              ))}
          </ChainOfThoughtSearchResults>
        )}
      </ChainOfThoughtStep>
    );
  } else if (name === "web_fetch") {
    const url = (args as { url: string })?.url;
    let title = url;
    if (typeof result === "string") {
      const potentialTitle = extractTitleFromMarkdown(result);
      if (potentialTitle && potentialTitle.toLowerCase() !== "untitled") {
        title = potentialTitle;
      }
    }
    return (
      <ChainOfThoughtStep
        key={id}
        label={resolveLabel(t.toolCalls.viewWebPage)}
        icon={GlobeIcon}
      >
        <ChainOfThoughtSearchResult>
          {url && (
            <a
              href={url}
              target="_blank"
              rel="noopener noreferrer"
              className="cursor-pointer"
            >
              {title}
            </a>
          )}
        </ChainOfThoughtSearchResult>
      </ChainOfThoughtStep>
    );
  } else if (name === "ls") {
    let description: string | undefined = (args as { description: string })
      ?.description;
    if (!description) {
      description = t.toolCalls.listFolder;
    }
    const path: string | undefined = (args as { path: string })?.path;
    return (
      <ChainOfThoughtStep
        key={id}
        label={resolveLabel(description)}
        icon={FolderOpenIcon}
      >
        {path && (
          <ChainOfThoughtSearchResult className="cursor-pointer">
            {path}
          </ChainOfThoughtSearchResult>
        )}
      </ChainOfThoughtStep>
    );
  } else if (name === "read_file") {
    let description: string | undefined = (args as { description: string })
      ?.description;
    if (!description) {
      description = t.toolCalls.readFile;
    }
    const { path } = args as { path: string; content: string };
    return (
      <ChainOfThoughtStep
        key={id}
        label={resolveLabel(description)}
        icon={BookOpenTextIcon}
      >
        {path && (
          <ChainOfThoughtSearchResult className="cursor-pointer">
            {path}
          </ChainOfThoughtSearchResult>
        )}
      </ChainOfThoughtStep>
    );
  } else if (name === "write_file" || name === "str_replace") {
    let description: string | undefined = (args as { description: string })
      ?.description;
    if (!description) {
      description = t.toolCalls.writeFile;
    }
    const path: string | undefined = (args as { path: string })?.path;
    if (isLoading && isLast && autoOpen && autoSelect && path && !result) {
      setTimeout(() => {
        const url = new URL(
          `write-file:${path}?message_id=${messageId}&tool_call_id=${id}`,
        ).toString();
        if (selectedArtifact === url) {
          return;
        }
        select(url, true);
        setOpen(true);
      }, 100);
    }

    return (
      <ChainOfThoughtStep
        key={id}
        className="cursor-pointer"
        label={resolveLabel(description)}
        icon={NotebookPenIcon}
        onClick={() => {
          select(
            new URL(
              `write-file:${path}?message_id=${messageId}&tool_call_id=${id}`,
            ).toString(),
          );
          setOpen(true);
        }}
      >
        {path && (
          <ChainOfThoughtSearchResult className="cursor-pointer">
            {path}
          </ChainOfThoughtSearchResult>
        )}
      </ChainOfThoughtStep>
    );
  } else if (name === "bash") {
    const description: string | undefined = (args as { description: string })
      ?.description;
    if (!description) {
      return (
        <ChainOfThoughtStep
          key={id}
          label={resolveLabel(t.toolCalls.executeCommand)}
          icon={SquareTerminalIcon}
        />
      );
    }
    const command: string | undefined = (args as { command: string })?.command;
    return (
      <ChainOfThoughtStep
        key={id}
        label={resolveLabel(description)}
        icon={SquareTerminalIcon}
      >
        {command && (
          <CodeBlock
            className="mx-0 cursor-pointer border-none px-0"
            showLineNumbers={false}
            language="bash"
            code={command}
          />
        )}
      </ChainOfThoughtStep>
    );
  } else if (name === "write_todos") {
    return (
      <ChainOfThoughtStep
        key={id}
        label={resolveLabel(t.toolCalls.writeTodos)}
        icon={ListTodoIcon}
      ></ChainOfThoughtStep>
    );
  } else {
    const description: string | undefined = (args as { description: string })
      ?.description;
    return (
      <ChainOfThoughtStep
        key={id}
        label={resolveLabel(description ?? t.toolCalls.useTool(name))}
        icon={WrenchIcon}
      ></ChainOfThoughtStep>
    );
  }
}

interface GenericCoTStep<T extends string = string> {
  id?: string;
  messageId?: string;
  type: T;
}

interface CoTReasoningStep extends GenericCoTStep<"reasoning"> {
  reasoning: string | null;
}

interface CoTToolCallStep extends GenericCoTStep<"toolCall"> {
  name: string;
  args: Record<string, unknown>;
  result?: string;
}

interface CoTAssistantTextStep extends GenericCoTStep<"assistantText"> {
  content: string;
}

type CoTStep = CoTReasoningStep | CoTToolCallStep | CoTAssistantTextStep;

function convertToSteps(messages: Message[]): CoTStep[] {
  const steps: CoTStep[] = [];
  for (const [messageIndex, message] of messages.entries()) {
    if (message.type === "ai") {
      const reasoning = extractReasoningContentFromMessage(message);
      if (reasoning) {
        const step: CoTReasoningStep = {
          id: message.id,
          messageId: message.id,
          type: "reasoning",
          reasoning,
        };
        steps.push(step);
      }
      // Clarification text/question render outside the panel (bubble + card).
      const isClarificationMessage =
        message.tool_calls?.some(
          (toolCall) => toolCall.name === "ask_clarification",
        ) === true;
      // Keep assistant text visible even without tool calls (#4304).
      const content = isClarificationMessage
        ? ""
        : extractContentFromMessage(message);
      if (content) {
        steps.push({
          id: `${message.id ?? `ai-${messageIndex}`}-content`,
          messageId: message.id,
          type: "assistantText",
          content,
        });
      }
      for (const tool_call of message.tool_calls ?? []) {
        if (
          tool_call.name === "task" ||
          tool_call.name === "ask_clarification"
        ) {
          continue;
        }
        const step: CoTToolCallStep = {
          id: tool_call.id,
          messageId: message.id,
          type: "toolCall",
          name: tool_call.name,
          args: tool_call.args,
        };
        const toolCallId = tool_call.id;
        if (toolCallId) {
          const toolCallResult = findToolCallResult(toolCallId, messages);
          if (toolCallResult) {
            try {
              const json = JSON.parse(toolCallResult);
              step.result = json;
            } catch {
              step.result = toolCallResult;
            }
          }
        }
        steps.push(step);
      }
    }
  }
  return steps;
}
