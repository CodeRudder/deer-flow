import {
  DownloadIcon,
  RotateCcwIcon,
  SquareArrowOutUpRightIcon,
  XIcon,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
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
  const [scale, setScale] = useState(1);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const dragStateRef = useRef<{
    pointerId: number;
    startX: number;
    startY: number;
    originX: number;
    originY: number;
  } | null>(null);

  const resetView = useCallback(() => {
    setScale(1);
    setOffset({ x: 0, y: 0 });
    dragStateRef.current = null;
  }, []);

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

  useEffect(() => {
    if (open) {
      resetView();
    }
  }, [open, resetView, src]);

  if (!open) return null;

  return createPortal(
    <div
      className="bg-background/90 fixed inset-0 z-[100] flex items-center justify-center overflow-hidden p-4 backdrop-blur-sm"
      onClick={onClose}
      onWheel={(event) => {
        event.preventDefault();
        event.stopPropagation();
        const zoomFactor = Math.exp(-event.deltaY * 0.0007);
        const nextScale = Math.min(6, Math.max(1, scale * zoomFactor));

        setScale(nextScale);
        if (nextScale === 1) {
          setOffset({ x: 0, y: 0 });
        }
      }}
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
        <Button
          variant="secondary"
          size="icon-sm"
          onClick={resetView}
          disabled={scale === 1 && offset.x === 0 && offset.y === 0}
        >
          <RotateCcwIcon />
        </Button>
        <Button variant="secondary" size="icon-sm" onClick={onClose}>
          <XIcon />
        </Button>
      </div>
      <div
        className="flex size-full touch-none items-center justify-center"
        onClick={(event) => event.stopPropagation()}
        onDoubleClick={resetView}
        onPointerDown={(event) => {
          if (scale <= 1) return;
          event.currentTarget.setPointerCapture(event.pointerId);
          dragStateRef.current = {
            pointerId: event.pointerId,
            startX: event.clientX,
            startY: event.clientY,
            originX: offset.x,
            originY: offset.y,
          };
        }}
        onPointerMove={(event) => {
          const dragState = dragStateRef.current;
          if (dragState?.pointerId !== event.pointerId) return;
          setOffset({
            x: dragState.originX + event.clientX - dragState.startX,
            y: dragState.originY + event.clientY - dragState.startY,
          });
        }}
        onPointerUp={(event) => {
          if (dragStateRef.current?.pointerId === event.pointerId) {
            dragStateRef.current = null;
          }
        }}
        onPointerCancel={() => {
          dragStateRef.current = null;
        }}
      >
        <img
          src={src}
          alt={alt}
          draggable={false}
          className="max-h-[92vh] max-w-[92vw] object-contain select-none"
          style={{
            cursor: scale > 1 ? "grab" : "zoom-in",
            transform: `translate3d(${offset.x}px, ${offset.y}px, 0) scale(${scale})`,
            transition: dragStateRef.current
              ? "none"
              : "transform 120ms ease-out",
          }}
        />
      </div>
    </div>,
    document.body,
  );
}
