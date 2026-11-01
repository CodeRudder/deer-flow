import {
  ChevronLeftIcon,
  ChevronRightIcon,
  DownloadIcon,
  MinusIcon,
  PlusIcon,
  RotateCcwIcon,
  SquareArrowOutUpRightIcon,
  XIcon,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

export type LightboxImage = {
  alt: string;
  downloadUrl?: string;
  openUrl?: string;
  src: string;
};

// Shared chrome for the overlay buttons: the backdrop is always dark, so the
// controls stay light regardless of the color theme.
const overlayButtonClass =
  "flex size-8 items-center justify-center rounded-full text-white transition-colors hover:bg-white/15 disabled:pointer-events-none disabled:opacity-40";

export function ImageLightbox({
  images,
  initialIndex = 0,
  onClose,
  open,
}: {
  images: LightboxImage[];
  initialIndex?: number;
  onClose: () => void;
  open: boolean;
}) {
  const [index, setIndex] = useState(initialIndex);
  const [scale, setScale] = useState(1);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const dragStateRef = useRef<{
    pointerId: number;
    startX: number;
    startY: number;
    originX: number;
    originY: number;
  } | null>(null);

  const current = images[index];
  const hasMultiple = images.length > 1;

  const goTo = useCallback(
    (next: number) => {
      setIndex(((next % images.length) + images.length) % images.length);
    },
    [images.length],
  );

  // Center-zoom shared by the wheel and the +/- buttons; snapping back to 1x
  // clears the pan so the image recenters like the wheel path does.
  const applyScale = useCallback((current: number, factor: number) => {
    const nextScale = Math.min(6, Math.max(1, current * factor));
    setScale(nextScale);
    if (nextScale === 1) {
      setOffset({ x: 0, y: 0 });
    }
  }, []);

  const zoomIn = useCallback(
    () => applyScale(scale, 1.25),
    [applyScale, scale],
  );
  const zoomOut = useCallback(
    () => applyScale(scale, 0.8),
    [applyScale, scale],
  );

  const resetView = useCallback(() => {
    setScale(1);
    setOffset({ x: 0, y: 0 });
    dragStateRef.current = null;
  }, []);

  useEffect(() => {
    if (open) {
      setIndex(initialIndex);
    }
  }, [initialIndex, open]);

  useEffect(() => {
    if (!open) return;

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      } else if (event.key === "ArrowRight" && hasMultiple) {
        goTo(index + 1);
      } else if (event.key === "ArrowLeft" && hasMultiple) {
        goTo(index - 1);
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [goTo, hasMultiple, index, onClose, open]);

  useEffect(() => {
    if (open) {
      resetView();
    }
  }, [open, resetView, index]);

  if (!open || !current) return null;

  return createPortal(
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center overflow-hidden bg-black/80 p-4"
      onClick={onClose}
      onWheel={(event) => {
        event.preventDefault();
        event.stopPropagation();
        applyScale(scale, Math.exp(-event.deltaY * 0.0007));
      }}
      role="dialog"
      aria-modal="true"
      aria-label={current.alt}
    >
      <div
        className="absolute top-8 left-1/2 z-10 flex -translate-x-1/2 items-center gap-1 rounded-full bg-white/10 p-1 backdrop-blur-sm"
        onClick={(event) => event.stopPropagation()}
      >
        {hasMultiple && (
          <span className="px-2 text-xs text-white/90 tabular-nums">
            {index + 1} / {images.length}
          </span>
        )}
        {current.openUrl && (
          <a
            aria-label="Open in new tab"
            className={overlayButtonClass}
            href={current.openUrl}
            target="_blank"
            rel="noopener noreferrer"
          >
            <SquareArrowOutUpRightIcon className="size-4" />
          </a>
        )}
        {current.downloadUrl && (
          <a
            aria-label="Download"
            className={overlayButtonClass}
            href={current.downloadUrl}
            target="_blank"
            rel="noopener noreferrer"
          >
            <DownloadIcon className="size-4" />
          </a>
        )}
        <button
          type="button"
          aria-label="Reset view"
          className={overlayButtonClass}
          onClick={resetView}
          disabled={scale === 1 && offset.x === 0 && offset.y === 0}
        >
          <RotateCcwIcon className="size-4" />
        </button>
        <button
          type="button"
          aria-label="Close"
          className={overlayButtonClass}
          onClick={onClose}
        >
          <XIcon className="size-4" />
        </button>
      </div>
      <div
        className="absolute bottom-8 left-1/2 z-10 flex -translate-x-1/2 items-center gap-1 rounded-full bg-white/10 p-1 backdrop-blur-sm"
        onClick={(event) => event.stopPropagation()}
      >
        <button
          type="button"
          aria-label="Zoom out"
          className={overlayButtonClass}
          onClick={zoomOut}
          disabled={scale <= 1}
        >
          <MinusIcon className="size-4" />
        </button>
        <button
          type="button"
          className="min-w-14 rounded-full px-1 text-xs text-white/90 tabular-nums transition-colors hover:bg-white/15"
          onClick={resetView}
          title="Reset zoom"
        >
          {Math.round(scale * 100)}%
        </button>
        <button
          type="button"
          aria-label="Zoom in"
          className={overlayButtonClass}
          onClick={zoomIn}
          disabled={scale >= 6}
        >
          <PlusIcon className="size-4" />
        </button>
      </div>
      {hasMultiple && (
        <>
          <button
            type="button"
            aria-label="Previous image"
            className="absolute top-1/2 left-8 z-10 flex size-10 -translate-y-1/2 items-center justify-center rounded-full bg-white/10 text-white backdrop-blur-sm transition-colors hover:bg-white/20"
            onClick={(event) => {
              event.stopPropagation();
              goTo(index - 1);
            }}
          >
            <ChevronLeftIcon className="size-5" />
          </button>
          <button
            type="button"
            aria-label="Next image"
            className="absolute top-1/2 right-8 z-10 flex size-10 -translate-y-1/2 items-center justify-center rounded-full bg-white/10 text-white backdrop-blur-sm transition-colors hover:bg-white/20"
            onClick={(event) => {
              event.stopPropagation();
              goTo(index + 1);
            }}
          >
            <ChevronRightIcon className="size-5" />
          </button>
        </>
      )}
      <div
        className="flex size-full touch-none items-center justify-center"
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
          src={current.src}
          alt={current.alt}
          draggable={false}
          className="max-h-[82vh] max-w-[86vw] object-contain select-none"
          onClick={(event) => event.stopPropagation()}
          onDoubleClick={resetView}
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
