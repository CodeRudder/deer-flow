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
} from "@/core/utils/files";
import { cn } from "@/lib/utils";

import { useArtifacts } from "./context";

export function ArtifactFileList({
  className,
  files,
  threadId,
}: {
  className?: string;
  files: string[];
  threadId: string;
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

  return (
    <ul className={cn("flex w-full flex-col gap-4", className)}>
      {files.map((file) => {
        const imageFile = isImageFile(file);
        const previewFailed = failedPreviewFiles.has(file);
        const artifactUrl = urlOfArtifact({ filepath: file, threadId });
        return (
          <Card
            key={file}
            className={cn(
              "relative cursor-pointer overflow-hidden p-3",
              imageFile && "gap-0 p-0",
            )}
            onClick={() => handleClick(file)}
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
                    onError={() => {
                      setFailedPreviewFiles((previous) => {
                        const next = new Set(previous);
                        next.add(file);
                        return next;
                      });
                    }}
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
                    onClick={(e) => handleInstallSkill(e, file)}
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
      })}
    </ul>
  );
}
