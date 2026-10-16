import { Film, Image, Music } from "lucide-react";
import {
  useCallback,
  useRef,
  useState,
  type KeyboardEvent,
  type MouseEvent,
  type PointerEvent,
  type ReactNode,
} from "react";

import { Checkbox } from "./forms";
import { cx } from "./cx";

/** A contact sheet of evenly spaced frames laid out row by row. */
export interface SpriteSheet {
  url: string;
  cols: number;
  rows: number;
  /** Number of frames actually used (the last row may be partly empty). */
  count: number;
}

export interface AssetTileProps {
  title: string;
  media: "video" | "photo" | "audio" | "other";
  poster: string | null;
  sprite?: SpriteSheet | null;
  /** Width / height of the poster; reserves the space so nothing shifts while images load. */
  aspectRatio?: number;
  durationLabel?: string | null;
  /** Small facts on the poster, for example "4K" or "25 fps". */
  facts?: string[];
  status?: ReactNode;
  /** Second line below the title. */
  meta?: ReactNode;
  selected?: boolean;
  selectLabel: string;
  /** Called on click / Enter. */
  onOpen: () => void;
  /** Called for modifier-click, Space and the checkbox. */
  onToggleSelect?: (modifiers: { shift: boolean; additive: boolean }) => void;
  tabIndex?: number;
  className?: string;
}

const FALLBACK_ICON = { video: Film, photo: Image, audio: Music, other: Film } as const;

function framePosition(frame: number, sprite: SpriteSheet): string {
  const col = frame % sprite.cols;
  const row = Math.floor(frame / sprite.cols);
  const x = sprite.cols > 1 ? (col / (sprite.cols - 1)) * 100 : 0;
  const y = sprite.rows > 1 ? (row / (sprite.rows - 1)) * 100 : 0;
  return `${x}% ${y}%`;
}

/**
 * Library tile: a poster that scrubs through the contact-sheet sprite while the pointer moves
 * across it, with duration and facts on the poster and a springy lift on hover.
 */
export function AssetTile({
  title,
  media,
  poster,
  sprite,
  aspectRatio = 16 / 9,
  durationLabel,
  facts,
  status,
  meta,
  selected = false,
  selectLabel,
  onOpen,
  onToggleSelect,
  tabIndex = 0,
  className,
}: AssetTileProps) {
  const [frame, setFrame] = useState<number | null>(null);
  const preloaded = useRef(false);
  const Fallback = FALLBACK_ICON[media];
  const scrubbable = sprite != null && sprite.count > 1;

  const preload = useCallback(() => {
    if (!sprite || preloaded.current) return;
    preloaded.current = true;
    const image = new window.Image();
    image.src = sprite.url;
  }, [sprite]);

  const scrub = (event: PointerEvent<HTMLElement>) => {
    if (!sprite || !scrubbable || event.pointerType === "touch") return;
    const rect = event.currentTarget.getBoundingClientRect();
    if (rect.width === 0) return;
    const ratio = Math.min(0.999, Math.max(0, (event.clientX - rect.left) / rect.width));
    const next = Math.floor(ratio * sprite.count);
    setFrame((current) => (current === next ? current : next));
  };

  const handleClick = (event: MouseEvent<HTMLButtonElement>) => {
    if (onToggleSelect && (event.shiftKey || event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      onToggleSelect({ shift: event.shiftKey, additive: event.ctrlKey || event.metaKey });
      return;
    }
    onOpen();
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === " " && onToggleSelect) {
      event.preventDefault();
      onToggleSelect({ shift: event.shiftKey, additive: true });
    }
  };

  return (
    <div className={cx("rb-tile", selected && "rb-tile--selected", className)}>
      <button
        type="button"
        className="rb-tile__media"
        style={{ aspectRatio }}
        tabIndex={tabIndex}
        aria-label={title}
        aria-pressed={onToggleSelect ? selected : undefined}
        onClick={handleClick}
        onKeyDown={handleKeyDown}
        onKeyUp={(event) => {
          if (event.key === " " && onToggleSelect) event.preventDefault();
        }}
        onPointerEnter={preload}
        onPointerMove={scrub}
        onPointerLeave={() => setFrame(null)}
        onBlur={() => setFrame(null)}
      >
        {poster ? (
          <img className="rb-tile__poster" src={poster} alt="" loading="lazy" decoding="async" />
        ) : (
          <span className="rb-tile__fallback" aria-hidden="true">
            <Fallback size={30} />
          </span>
        )}
        {sprite && frame !== null ? (
          <span
            className="rb-tile__sprite"
            aria-hidden="true"
            style={{
              backgroundImage: `url(${sprite.url})`,
              backgroundSize: `${sprite.cols * 100}% ${sprite.rows * 100}%`,
              backgroundPosition: framePosition(frame, sprite),
            }}
          />
        ) : null}
        {sprite && frame !== null ? (
          <span
            className="rb-tile__scrub"
            aria-hidden="true"
            style={{ width: `${((frame + 1) / sprite.count) * 100}%` }}
          />
        ) : null}
        {durationLabel ? <span className="rb-tile__duration">{durationLabel}</span> : null}
        {facts && facts.length > 0 ? (
          <span className="rb-tile__facts">
            {facts.map((fact) => (
              <span key={fact}>{fact}</span>
            ))}
          </span>
        ) : null}
      </button>
      {onToggleSelect ? (
        <span className="rb-tile__select">
          <Checkbox
            checked={selected}
            onCheckedChange={() => onToggleSelect({ shift: false, additive: true })}
            aria-label={selectLabel}
            tabIndex={-1}
          />
        </span>
      ) : null}
      <div className="rb-tile__body">
        <div className="rb-tile__title" title={title}>
          {title}
        </div>
        <div className="rb-tile__meta">
          {meta ? <span className="rb-tile__meta-text">{meta}</span> : null}
          {status}
        </div>
      </div>
    </div>
  );
}
