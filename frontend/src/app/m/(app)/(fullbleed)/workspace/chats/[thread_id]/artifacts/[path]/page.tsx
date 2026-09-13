"use client";

import type { Message } from "@langchain/langgraph-sdk";
import type { BaseStream } from "@langchain/langgraph-sdk/react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeftIcon,
  CheckIcon,
  ChevronDownIcon,
  FileIcon,
} from "lucide-react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import {
  ModelSelector,
  ModelSelectorContent,
  ModelSelectorInput,
  ModelSelectorItem,
  ModelSelectorList,
  ModelSelectorTrigger,
} from "@/components/ai-elements/model-selector";
import { ArtifactFilePreview } from "@/components/workspace/artifacts";
import { ImageLightbox } from "@/components/workspace/artifacts/image-lightbox";
import { ThreadContext } from "@/components/workspace/messages/context";
import { MobileArtifactActionsMenu } from "@/components/workspace/mobile/artifact-actions-menu";
import {
  artifactDisplayName,
  artifactHref,
  artifactViewMode,
  decodeArtifactParam,
  isDownloadableArtifact,
  resolvedArtifactPath,
} from "@/components/workspace/mobile/artifact-navigation";
import { getAPIClient } from "@/core/api";
import { useArtifactContent } from "@/core/artifacts/hooks";
import { isWriteFileArtifact } from "@/core/artifacts/preview";
import { urlOfArtifact } from "@/core/artifacts/utils";
import { writeTextToClipboard } from "@/core/clipboard";
import { useI18n } from "@/core/i18n/hooks";
import type { AgentThreadState } from "@/core/threads";
import { cn } from "@/lib/utils";

// Both stylesheets, not just the surface: both dialogs on this screen — the
// file switcher and the ⋯ tools menu — are the model dialog, whose touch rules
// live in their own file because the chat route has a different set of them
// (see `mobile-dialog.css`). Without this import a cold load of this URL — no
// chunk from the chat route in memory — renders 36px rows and a 14px search
// box.
import "@/components/workspace/mobile/artifact-surface.css";
import "@/components/workspace/mobile/mobile-dialog.css";

/**
 * Mobile full-screen artifact view (T6, prototype ⑤).
 *
 * The desktop shows an artifact in a draggable 60/40 split (`chat-box.tsx`); a
 * 390px column cannot hold two panes, so the artifact gets the whole screen and
 * is reached by a route change instead of a panel toggle.
 *
 * What is *rendered* is not re-implemented: HTML and markdown go through the
 * shared `ArtifactFilePreview` (sandboxed iframe, injected `<base href>`,
 * scroll-position round trip) and images through the shared `ImageLightbox`.
 * Only the chrome around them is mobile-specific, because the desktop's chrome
 * — a file `Select`, a code/preview `ToggleGroup`, 32px toolbar buttons — is
 * built for a mouse.
 *
 * The route is public (`/workspace/chats/<id>/artifacts/<path>`): the
 * middleware re-lands a phone on this file, so `/m/` never reaches the address
 * bar (plan §1.2.1).
 */
export default function MobileArtifactPage() {
  const params = useParams<{ thread_id: string; path: string }>();
  const searchParams = useSearchParams();
  const { t } = useI18n();
  const isMock = searchParams.get("mock") === "true";
  const threadId = params.thread_id;
  // The identifier lives in a single, percent-encoded segment: it is a path
  // (and, for a `write_file` step, a `write-file:` URL), not a route.
  const filepath = useMemo(
    () => decodeArtifactParam(params.path ?? ""),
    [params.path],
  );

  const thread = useArtifactThread(threadId, isMock);

  // The reused readers take a `ThreadContext`, whose value is a whole LangGraph
  // stream. This screen has no stream — it is a different route and does not
  // mount the chat — but it can supply the one shape they read: the transcript
  // a `write-file:` draft is built from, plus the loading flag. Building that
  // here is what lets the rendering be reused instead of re-implemented.
  const threadContextValue = useMemo(
    () => ({
      thread: {
        messages: thread.messages,
        values: { messages: thread.messages, artifacts: thread.artifacts },
        isLoading: thread.isLoading,
      } as unknown as BaseStream<AgentThreadState>,
      isMock,
    }),
    [isMock, thread.artifacts, thread.isLoading, thread.messages],
  );

  return (
    <ThreadContext.Provider value={threadContextValue}>
      {thread.isError ? (
        <div className="flex h-full items-center justify-center p-6">
          <p
            data-testid="mobile-artifact-missing"
            className="text-muted-foreground text-center text-sm"
          >
            {t.artifactViewer.loadFailed}
          </p>
        </div>
      ) : (
        <ArtifactScreen
          artifacts={thread.artifacts}
          filepath={filepath}
          isMock={isMock}
          threadId={threadId}
        />
      )}
    </ThreadContext.Provider>
  );
}

/** Views whose content is fetched (or drafted) rather than linked to. */
const NEEDS_CONTENT = new Set(["html", "markdown", "code"]);

function ArtifactScreen({
  artifacts,
  filepath,
  isMock,
  threadId,
}: {
  artifacts: string[];
  filepath: string;
  isMock: boolean;
  threadId: string;
}) {
  const { t } = useI18n();
  const router = useRouter();
  const queryClient = useQueryClient();
  const [wrap, setWrap] = useState(false);
  const [lightboxOpen, setLightboxOpen] = useState(false);
  // The file switcher is a `ModelSelector`, i.e. an uncontrolled Radix Dialog
  // by default: `CommandItem` does not close its parent dialog (cmdk's click
  // handler only reports the selection), and nothing here unmounts the dialog
  // either — picking the file that is already open changes no URL at all, so
  // there is no navigation to tear it down. Held open, it would cover the file
  // the operator just chose. Controlled, with the close in `onSelect`.
  const [switcherOpen, setSwitcherOpen] = useState(false);

  const mode = useMemo(() => artifactViewMode(filepath), [filepath]);
  const resolvedPath = useMemo(
    () => resolvedArtifactPath(filepath),
    [filepath],
  );
  const isWriteFile = useMemo(() => isWriteFileArtifact(filepath), [filepath]);
  const displayName = useMemo(() => artifactDisplayName(filepath), [filepath]);
  const downloadable = isDownloadableArtifact(filepath);

  const artifactUrl = useMemo(
    () => urlOfArtifact({ filepath: resolvedPath, threadId, isMock }),
    [isMock, resolvedPath, threadId],
  );
  const downloadUrl = useMemo(
    () =>
      urlOfArtifact({
        filepath: resolvedPath,
        threadId,
        download: true,
        isMock,
      }),
    [isMock, resolvedPath, threadId],
  );

  const { content, url, isLoading } = useArtifactContent({
    threadId,
    filepath,
    enabled: NEEDS_CONTENT.has(mode) && !isWriteFile,
  });

  // The lightbox is portalled onto `document.body`, so a wrapper class here
  // cannot reach it; this body marker is the scope marker for the mobile-only
  // stylesheet in `artifact-surface.css`. The desktop never sets it.
  useEffect(() => {
    if (!lightboxOpen) {
      return;
    }
    document.body.classList.add("mobile-artifact-lightbox-open");
    return () => {
      document.body.classList.remove("mobile-artifact-lightbox-open");
    };
  }, [lightboxOpen]);

  const handleCopy = useMemo(() => {
    if (mode !== "code") {
      return undefined;
    }
    return () => {
      void (async () => {
        const didCopy = await writeTextToClipboard(content ?? "");
        if (!didCopy) {
          toast.error(t.clipboard.failedToCopyToClipboard);
          return;
        }
        toast.success(t.clipboard.copiedToClipboard);
      })().catch(() => {
        toast.error(t.clipboard.failedToCopyToClipboard);
      });
    };
  }, [content, mode, t.clipboard]);

  /**
   * 刷新: re-fetch what this screen shows (A5).
   *
   * Two sources back the bytes, and a draft uses the second one: the artifact
   * query for a file on the backend, and the thread state for a `write-file:`
   * draft, whose content is assembled from the transcript. Both are
   * invalidated so either kind of view refreshes; the keys are the ones
   * `useArtifactContent` and `useArtifactThread` register, matched by prefix.
   */
  const handleRefresh = useMemo(() => {
    return () => {
      void queryClient.invalidateQueries({
        queryKey: ["artifact", filepath, threadId, isMock],
      });
      void queryClient.invalidateQueries({
        queryKey: ["mobile-artifact-thread", threadId, isMock],
      });
    };
  }, [filepath, isMock, queryClient, threadId]);

  return (
    <div className="mobile-artifact-surface flex h-full min-h-0 flex-col">
      <header className="bg-background/95 sticky top-0 z-20 flex shrink-0 items-center gap-1 border-b px-1 py-1 supports-backdrop-filter:backdrop-blur">
        {/* The public chat path: the middleware re-lands it on the mobile tree. */}
        <Link
          href={`/workspace/chats/${threadId}`}
          aria-label={t.artifactViewer.back}
          data-testid="mobile-artifact-back"
          className="active:bg-accent flex size-11 shrink-0 items-center justify-center rounded-full"
        >
          <ArrowLeftIcon className="size-5" />
        </Link>
        {/* The current file is the switcher (prototype ⑤): tapping the name
            opens the thread's file list. The desktop does the same thing with a
            file `Select` in the panel header, so this is the mobile form of an
            existing control rather than a new one.

            The layer is the model picker's own `ModelSelector` — a `Command`
            list with a search box — because a thread can easily write a dozen
            files and a plain menu has no way to find one. It is a dialog rather
            than a dropdown anchored under the title: `ui/popover.tsx` does not
            exist in this repo and `ui/` is registry-generated, so an anchored
            popover is not a component this tree may hand-roll. The trigger is
            still the title, which is what the plan asks for.

            One file, one label — no chevron and no picker, because there is
            nothing to switch to. */}
        <div className="min-w-0 flex-1">
          {artifacts.length > 1 ? (
            <ModelSelector open={switcherOpen} onOpenChange={setSwitcherOpen}>
              <ModelSelectorTrigger asChild>
                <button
                  type="button"
                  data-testid="mobile-artifact-switcher"
                  aria-label={t.artifactViewer.switchFile(displayName)}
                  // `min-h-11` is the touch floor the back button and the
                  // header's 文件 button already clear; `py-1` stays as the
                  // visual padding inside that box. The title and the count
                  // below it keep their own lines.
                  className="active:bg-accent flex min-h-11 max-w-full min-w-0 items-center gap-1 rounded-lg py-1 text-left"
                >
                  <span
                    data-testid="mobile-artifact-title"
                    className="min-w-0 truncate text-[15px] font-medium"
                  >
                    {displayName}
                  </span>
                  <ChevronDownIcon
                    aria-hidden="true"
                    className="text-muted-foreground size-4 shrink-0"
                  />
                </button>
              </ModelSelectorTrigger>
              <ModelSelectorContent
                className="mobile-model-dialog"
                title={t.common.artifacts}
                // A file list, not a model chooser: without this the dialog
                // reads out the registry's model sentence — in English — in a
                // Chinese interface.
                description={t.artifactViewer.searchFiles}
              >
                <ModelSelectorInput
                  placeholder={t.artifactViewer.searchFiles}
                />
                <ModelSelectorList className="max-h-80 p-1">
                  {artifacts.map((artifact) => (
                    <ModelSelectorItem
                      key={artifact}
                      // `value` is what the search box matches on, so it carries
                      // the whole identifier: the basename alone would miss a
                      // file whose directories are what the operator remembers.
                      value={`${artifactDisplayName(artifact)} ${artifact}`}
                      data-testid={`mobile-artifact-option-${artifactDisplayName(artifact)}`}
                      // Deliberately *not* `modelOptionRowClassName`: prototype
                      // ⑤'s `.switcher .ditem` keeps every file at
                      // `--foreground` and marks the current one with weight,
                      // whereas the model helper dims the unselected rows. A
                      // file list is navigation, not a choice among equals —
                      // there is no "not chosen" reading to grey out. Only the
                      // shared 36px row height stays.
                      className={cn(
                        "text-foreground h-9",
                        artifact === resolvedPath && "font-semibold",
                      )}
                      onSelect={() => {
                        // Closing is this component's job: selecting the file
                        // that is already open leaves the URL untouched, so
                        // nothing else would.
                        setSwitcherOpen(false);
                        router.push(
                          artifactHref(threadId, artifact, { isMock }),
                        );
                      }}
                    >
                      <FileIcon
                        aria-hidden="true"
                        className="text-muted-foreground size-4 shrink-0"
                      />
                      <span className="min-w-0 flex-1 truncate">
                        {artifactDisplayName(artifact)}
                      </span>
                      {artifact === resolvedPath && (
                        <CheckIcon
                          aria-hidden="true"
                          className="size-4 shrink-0"
                        />
                      )}
                    </ModelSelectorItem>
                  ))}
                </ModelSelectorList>
              </ModelSelectorContent>
            </ModelSelector>
          ) : (
            <h1
              data-testid="mobile-artifact-title"
              className="truncate text-[15px] font-medium"
            >
              {displayName}
            </h1>
          )}
          {artifacts.length > 0 && (
            <p className="text-muted-foreground truncate text-xs">
              {t.artifactViewer.count(artifacts.length)}
            </p>
          )}
        </div>
        {/* ⑤: the title bar's third item. There is no bottom bar under the
            document any more — these tools live in this button's menu (⑤b),
            which frees the 56px the bar held for the document itself. */}
        <MobileArtifactActionsMenu
          downloadUrl={downloadable ? downloadUrl : undefined}
          openUrl={downloadable ? artifactUrl : undefined}
          // 分享 needs both: the bytes to fetch, and the name the share sheet
          // shows. A `write-file:` draft has neither — it is not a file yet.
          fileName={downloadable ? displayName : undefined}
          wrap={mode === "code" ? wrap : undefined}
          onWrapChange={mode === "code" ? setWrap : undefined}
          onCopy={handleCopy}
          copyDisabled={!content}
          // Only the views whose bytes this screen fetches: refreshing an image
          // or an iframe would mean re-mounting it, not re-fetching content.
          onRefresh={NEEDS_CONTENT.has(mode) ? handleRefresh : undefined}
        />
      </header>

      <div className="relative min-h-0 flex-1">
        {mode === "html" && (
          <ArtifactFilePreview
            content={content ?? ""}
            language="html"
            scrollKey={filepath}
            url={url}
          />
        )}

        {mode === "markdown" && (
          // `ArtifactFilePreview` fills its parent for markdown, so the scroll
          // container has to sit outside it.
          <div className="size-full overflow-y-auto">
            <ArtifactFilePreview
              content={content ?? ""}
              language="markdown"
              scrollKey={filepath}
              url={url}
            />
          </div>
        )}

        {mode === "code" && (
          <pre
            data-testid="mobile-artifact-code"
            data-wrap={wrap ? "true" : "false"}
            className={cn(
              "size-full overflow-auto p-4 font-mono text-[13px] leading-relaxed",
              wrap ? "break-words whitespace-pre-wrap" : "whitespace-pre",
            )}
          >
            {isLoading ? t.common.loading : (content ?? "")}
          </pre>
        )}

        {mode === "image" && (
          <div className="bg-muted/20 flex size-full items-center justify-center overflow-hidden p-4">
            <img
              data-testid="mobile-artifact-image"
              className="max-h-full max-w-full cursor-zoom-in object-contain"
              src={artifactUrl}
              alt={displayName}
              onClick={() => setLightboxOpen(true)}
            />
            <ImageLightbox
              images={[
                {
                  alt: displayName,
                  downloadUrl,
                  openUrl: artifactUrl,
                  src: artifactUrl,
                },
              ]}
              onClose={() => setLightboxOpen(false)}
              open={lightboxOpen}
            />
          </div>
        )}

        {mode === "iframe" && (
          <iframe className="size-full" title={displayName} src={artifactUrl} />
        )}

        {mode === "download" && (
          <div className="flex size-full items-center justify-center p-6">
            <p
              data-testid="mobile-artifact-download-only"
              className="text-muted-foreground max-w-xs text-center text-sm"
            >
              {t.artifactViewer.downloadOnly}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

/** Stable empties, so the context value above does not change every render. */
const EMPTY_MESSAGES: Message[] = [];
const EMPTY_ARTIFACTS: string[] = [];

/**
 * The transcript tail of the thread the artifact belongs to.
 *
 * A `write-file:` artifact — what a `write_file` step in the transcript links
 * to — is not a file on the backend at all: an in-progress draft exists only in
 * the tool call's arguments, so the screen needs the messages even though it is
 * not the chat screen. One `getState` call answers both that and the artifact
 * list behind the tab strip.
 */
function useArtifactThread(threadId: string, isMock: boolean) {
  const apiClient = getAPIClient(isMock);
  const query = useQuery<{ messages: Message[]; artifacts: string[] }>({
    queryKey: ["mobile-artifact-thread", threadId, isMock],
    queryFn: async () => {
      const state =
        await apiClient.threads.getState<AgentThreadState>(threadId);
      const values = state.values ?? ({} as AgentThreadState);
      return {
        messages: values.messages ?? [],
        artifacts: values.artifacts ?? [],
      };
    },
    enabled: Boolean(threadId),
    retry: false,
    refetchOnWindowFocus: false,
  });

  return {
    messages: query.data?.messages ?? EMPTY_MESSAGES,
    artifacts: query.data?.artifacts ?? EMPTY_ARTIFACTS,
    isLoading: query.isPending,
    isError: query.isError,
  };
}
