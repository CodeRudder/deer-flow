import { DownloadIcon, LoaderIcon, PackageIcon } from "lucide-react";
import { useCallback, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardAction,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { urlOfArtifact } from "@/core/artifacts/utils";
import { useI18n } from "@/core/i18n/hooks";
import { installSkill } from "@/core/skills/api";
import {
  getFileExtensionDisplayName,
  getFileIcon,
  getFileName,
  isImageFile,
  isVideoFile,
} from "@/core/utils/files";
import { cn } from "@/lib/utils";

import { useArtifacts } from "./context";

export function ArtifactFileList({
  className,
  files,
  threadId,
  variant = "list",
}: {
  className?: string;
  files: string[];
  threadId: string;
  variant?: "list" | "message";
}) {
  const { t } = useI18n();
  const { select: selectArtifact, setOpen } = useArtifacts();
  const [installingFile, setInstallingFile] = useState<string | null>(null);
  const [failedPreviewFiles, setFailedPreviewFiles] = useState<Set<string>>(
    () => new Set(),
  );

  const handleClick = useCallback(
    (filepath: string) => {
      selectArtifact(filepath);
      setOpen(true);
    },
    [selectArtifact, setOpen],
  );

  const markPreviewFailed = useCallback((file: string) => {
    setFailedPreviewFiles((previous) => new Set(previous).add(file));
  }, []);

  const handleInstallSkill = useCallback(
    async (e: React.MouseEvent, filepath: string) => {
      e.stopPropagation();
      e.preventDefault();

      if (installingFile) return;

      setInstallingFile(filepath);
      try {
        const result = await installSkill({
          thread_id: threadId,
          path: filepath,
        });
        if (result.success) {
          toast.success(result.message);
        } else {
          toast.error(result.message || "Failed to install skill");
        }
      } catch (error) {
        console.error("Failed to install skill:", error);
        toast.error("Failed to install skill");
      } finally {
        setInstallingFile(null);
      }
    },
    [threadId, installingFile],
  );

  const imageFiles = files.filter(isImageFile);
  const videoFiles = files.filter(isVideoFile);
  const nonMediaFiles = files.filter(
    (file) => !isImageFile(file) && !isVideoFile(file),
  );

  if (variant === "message") {
    return (
      <div className={cn("flex w-full flex-col gap-3", className)}>
        {imageFiles.length > 0 && (
          <div className="flex flex-col items-start gap-3">
            {imageFiles.map((file) => (
              <ArtifactImagePreview
                key={file}
                file={file}
                threadId={threadId}
                onClick={() => handleClick(file)}
                onError={() => markPreviewFailed(file)}
                previewFailed={failedPreviewFiles.has(file)}
              />
            ))}
          </div>
        )}
        {videoFiles.length > 0 && (
          <div className="flex flex-col items-start gap-3">
            {videoFiles.map((file) => (
              <video
                key={file}
                src={urlOfArtifact({ filepath: file, threadId })}
                controls
                preload="metadata"
                className="max-h-[min(70vh,560px)] max-w-full rounded-md"
              />
            ))}
          </div>
        )}
        {nonMediaFiles.length > 0 && (
          <ul className="flex w-full flex-col gap-3">
            {nonMediaFiles.map((file) => (
              <ArtifactFileCard
                key={file}
                file={file}
                installingFile={installingFile}
                onClick={() => handleClick(file)}
                onInstallSkill={handleInstallSkill}
                t={t}
                threadId={threadId}
              />
            ))}
          </ul>
        )}
      </div>
    );
  }

  return (
    <ul className={cn("flex w-full flex-col gap-4", className)}>
      {files.map((file) => (
        <ArtifactFileCard
          key={file}
          file={file}
          installingFile={installingFile}
          onClick={() => handleClick(file)}
          onImageError={() => markPreviewFailed(file)}
          onInstallSkill={handleInstallSkill}
          previewFailed={failedPreviewFiles.has(file)}
          t={t}
          threadId={threadId}
        />
      ))}
    </ul>
  );
}

function ArtifactImagePreview({
  file,
  onClick,
  onError,
  previewFailed,
  threadId,
}: {
  file: string;
  onClick: () => void;
  onError: () => void;
  previewFailed: boolean;
  threadId: string;
}) {
  const artifactUrl = urlOfArtifact({ filepath: file, threadId });

  if (previewFailed) {
    return (
      <button
        type="button"
        className="border-border/50 bg-background text-muted-foreground flex max-w-full items-center gap-2 rounded-md border px-3 py-2 text-sm"
        onClick={onClick}
      >
        {getFileIcon(file, "size-4")}
        <span className="min-w-0 truncate">{getFileName(file)}</span>
      </button>
    );
  }

  return (
    <button
      type="button"
      className="group block max-w-full overflow-hidden rounded-md text-left"
      onClick={onClick}
    >
      <img
        src={artifactUrl}
        alt={getFileName(file)}
        className="max-h-[min(70vh,560px)] max-w-full rounded-md object-contain transition-opacity group-hover:opacity-90"
        loading="lazy"
        onError={onError}
      />
    </button>
  );
}

function ArtifactFileCard({
  file,
  installingFile,
  onClick,
  onImageError,
  onInstallSkill,
  previewFailed = false,
  t,
  threadId,
}: {
  file: string;
  installingFile: string | null;
  onClick: () => void;
  onImageError?: () => void;
  onInstallSkill: (e: React.MouseEvent, filepath: string) => void;
  previewFailed?: boolean;
  t: ReturnType<typeof useI18n>["t"];
  threadId: string;
}) {
  const imageFile = isImageFile(file);
  const artifactUrl = imageFile
    ? urlOfArtifact({ filepath: file, threadId })
    : undefined;

  return (
    <Card
      className={cn(
        "relative cursor-pointer overflow-hidden p-3",
        imageFile && "gap-0 p-0",
      )}
      onClick={onClick}
    >
      {imageFile && (
        <div className="bg-muted/20 flex aspect-square w-full items-center justify-center overflow-hidden">
          {previewFailed ? (
            <div className="text-muted-foreground flex flex-col items-center gap-2 px-4 text-center text-xs">
              {getFileIcon(file, "size-8")}
              <span className="line-clamp-2 [overflow-wrap:anywhere]">
                {getFileName(file)}
              </span>
            </div>
          ) : (
            <img
              src={artifactUrl}
              alt={getFileName(file)}
              className="size-full object-contain"
              loading="lazy"
              onError={onImageError}
            />
          )}
        </div>
      )}
      <CardHeader
        className={cn(
          "grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 pr-2 pl-1",
          imageFile && "p-3",
        )}
      >
        <CardTitle
          className={cn(
            "relative min-w-0 leading-tight [overflow-wrap:anywhere] break-words",
            !imageFile && "pl-8",
          )}
        >
          <div className="min-w-0">{getFileName(file)}</div>
          {!imageFile && (
            <div className="absolute top-2 -left-0.5">
              {getFileIcon(file, "size-6")}
            </div>
          )}
        </CardTitle>
        <CardDescription
          className={cn("min-w-0 text-xs", !imageFile && "pl-8")}
        >
          {getFileExtensionDisplayName(file)} file
        </CardDescription>
        <CardAction className="row-span-1 self-center">
          {file.endsWith(".skill") && (
            <Button
              variant="ghost"
              disabled={installingFile === file}
              onClick={(e) => onInstallSkill(e, file)}
            >
              {installingFile === file ? (
                <LoaderIcon className="size-4 animate-spin" />
              ) : (
                <PackageIcon className="size-4" />
              )}
              {t.common.install}
            </Button>
          )}
          <Button variant="ghost" asChild>
            <a
              href={urlOfArtifact({
                filepath: file,
                threadId: threadId,
                download: true,
              })}
              target="_blank"
              rel="noopener noreferrer"
              onClick={(e) => e.stopPropagation()}
            >
              <DownloadIcon className="size-4" />
              {t.common.download}
            </a>
          </Button>
        </CardAction>
      </CardHeader>
    </Card>
  );
}
