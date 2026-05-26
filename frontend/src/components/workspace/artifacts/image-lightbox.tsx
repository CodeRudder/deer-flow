import { DownloadIcon, SquareArrowOutUpRightIcon, XIcon } from "lucide-react";
import { useEffect } from "react";
import { createPortal } from "react-dom";

import { Button } from "@/components/ui/button";

export function ImageLightbox({
  alt,
  downloadUrl,
  onClose,
  open,
  openUrl,
  src,
}: {
  alt: string;
  downloadUrl?: string;
  onClose: () => void;
  open: boolean;
  openUrl?: string;
  src: string;
}) {
  useEffect(() => {
    if (!open) return;

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [onClose, open]);

  if (!open) return null;

  return createPortal(
    <div
      className="bg-background/90 fixed inset-0 z-[100] flex items-center justify-center p-4 backdrop-blur-sm"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={alt}
    >
      <div
        className="absolute top-4 right-4 flex items-center gap-2"
        onClick={(event) => event.stopPropagation()}
      >
        {openUrl && (
          <Button variant="secondary" size="icon-sm" asChild>
            <a href={openUrl} target="_blank" rel="noopener noreferrer">
              <SquareArrowOutUpRightIcon />
            </a>
          </Button>
        )}
        {downloadUrl && (
          <Button variant="secondary" size="icon-sm" asChild>
            <a href={downloadUrl} target="_blank" rel="noopener noreferrer">
              <DownloadIcon />
            </a>
          </Button>
        )}
        <Button variant="secondary" size="icon-sm" onClick={onClose}>
          <XIcon />
        </Button>
      </div>
      <img
        src={src}
        alt={alt}
        className="max-h-[92vh] max-w-[92vw] object-contain"
        onClick={(event) => event.stopPropagation()}
      />
    </div>,
    document.body,
  );
}
