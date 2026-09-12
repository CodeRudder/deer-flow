"use client";

import type { Message } from "@langchain/langgraph-sdk";
import type { BaseStream } from "@langchain/langgraph-sdk/react";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeftIcon } from "lucide-react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { ArtifactFilePreview } from "@/components/workspace/artifacts";
import { ImageLightbox } from "@/components/workspace/artifacts/image-lightbox";
import { ThreadContext } from "@/components/workspace/messages/context";
import { MobileArtifactActions } from "@/components/workspace/mobile/artifact-actions";
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

import "@/components/workspace/mobile/artifact-surface.css";

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
  const [wrap, setWrap] = useState(false);
  const [lightboxOpen, setLightboxOpen] = useState(false);

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
        <div className="min-w-0 flex-1">
          <h1
            data-testid="mobile-artifact-title"
            className="truncate text-[15px] font-medium"
          >
            {displayName}
          </h1>
          {artifacts.length > 0 && (
            <p className="text-muted-foreground truncate text-xs">
              {t.artifactViewer.count(artifacts.length)}
            </p>
          )}
        </div>
      </header>

      {/* The thread's other artifacts (prototype ⑤'s tab strip); the desktop's
          equivalent is the panel header's file `Select`. */}
      {artifacts.length > 1 && (
        <nav
          aria-label={t.common.artifacts}
          data-testid="mobile-artifact-tabs"
          className="flex shrink-0 gap-1 overflow-x-auto border-b px-2"
        >
          {artifacts.map((artifact) => {
            const active = artifact === resolvedPath;
            return (
              <Link
                key={artifact}
                href={artifactHref(threadId, artifact, { isMock })}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "flex min-h-11 shrink-0 items-center border-b-2 px-3 text-sm",
                  active
                    ? "border-foreground text-foreground font-medium"
                    : "text-muted-foreground border-transparent",
                )}
              >
                {artifactDisplayName(artifact)}
              </Link>
            );
          })}
        </nav>
      )}

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

      <MobileArtifactActions
        downloadUrl={downloadable ? downloadUrl : undefined}
        openUrl={downloadable ? artifactUrl : undefined}
        wrap={mode === "code" ? wrap : undefined}
        onWrapChange={mode === "code" ? setWrap : undefined}
        onCopy={handleCopy}
        copyDisabled={!content}
      />
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
