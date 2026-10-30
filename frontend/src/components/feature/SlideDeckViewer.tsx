// ============================================================================
// SlideDeckViewer — draws an uploaded PowerPoint deck inline, one slide at a
// time, from the model produced by /curriculum_api/curriculum/presentations/.
//
// Each slide is laid out on a stage of the deck's real pixel size (1280×720 for
// a 16:9 deck) with every shape absolutely positioned, then the whole stage is
// CSS-scaled to the available width. That keeps one set of coordinates —
// PowerPoint's — and lets the browser do the fitting, so the deck stays
// readable from a phone to a wide monitor.
// ============================================================================
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { AppIcon } from '@/components/feature/AppIcon';
import {
  fetchSlideDeck,
  type Slide,
  type SlideDeck,
  type SlideParagraph,
  type SlideShape,
  type SlideTableCell,
} from '@/api/slideDeck';

/** Indent per outline level, in stage pixels (PowerPoint's default is 0.25"). */
const INDENT_PX = 24;

interface SlideDeckViewerProps {
  /** Called after the displayed document's first page and images are ready. */
  onReady?: () => void;
  /** The deck's authored resource URL (a site-relative upload path). */
  src: string;
  title: string;
  /** Shown instead of the deck when the server cannot render it. */
  fallback: (reason: string) => React.ReactNode;
}

export function SlideDeckViewer({ src, title, fallback, onReady }: SlideDeckViewerProps) {
  const [deck, setDeck] = useState<SlideDeck | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [index, setIndex] = useState(0);
  const [showNotes, setShowNotes] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    setDeck(null);
    setError(null);
    setIndex(0);
    fetchSlideDeck(src, controller.signal)
      .then(result => { if (!controller.signal.aborted) setDeck(result); })
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return;
        if (cause instanceof DOMException && cause.name === 'AbortError') return;
        setError(cause instanceof Error ? cause.message : 'The slides could not be loaded.');
      });
    return () => controller.abort();
  }, [src]);

  const slideCount = deck?.slides.length ?? 0;
  const step = useCallback((delta: number) => {
    setIndex(current => Math.min(Math.max(current + delta, 0), Math.max(slideCount - 1, 0)));
  }, [slideCount]);

  if (error) return <>{fallback(error)}</>;
  if (!deck) {
    return (
      <div
        className="rounded-xl border border-background-300 bg-background-100 animate-pulse"
        style={{ aspectRatio: '16 / 9' }}
        role="status"
        aria-label={`Loading ${title}`}
      />
    );
  }
  if (!slideCount) return <>{fallback('This document has no pages to show.')}</>;

  const slide = deck.slides[Math.min(index, slideCount - 1)];
  // A rendered PDF is pages; a deck is slides. Same component either way.
  const unit = deck.unit === 'page' ? 'Page' : 'Slide';
  return (
    <div>
      {/* Arrow keys page through the deck once the viewer has focus — a global
          listener would fight the page's own scrolling. */}
      <div
        tabIndex={0}
        role="group"
        aria-label={`${title}: ${unit.toLowerCase()} ${slide.number} of ${slideCount}`}
        onKeyDown={event => {
          if (event.key === 'ArrowRight' || event.key === 'PageDown') { step(1); event.preventDefault(); }
          if (event.key === 'ArrowLeft' || event.key === 'PageUp') { step(-1); event.preventDefault(); }
        }}
        className="rounded-xl outline-none focus-visible:ring-2 focus-visible:ring-orange-500"
      >
        <SlideStage slide={slide} widthPx={deck.slideWidthPx} heightPx={deck.slideHeightPx} onReady={onReady} />
      </div>

      {deck.truncated && deck.totalPages && (
        <p className="mt-3 rounded-lg border border-background-200 bg-background-50 p-2.5 text-[11px] text-foreground-500">
          Showing the first {slideCount} of {deck.totalPages} pages. Open or download the
          file below to read the rest.
        </p>
      )}

      {deck.sourceMissing && (
        <p className="mt-3 flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">
          <AppIcon className="ri-error-warning-line mt-0.5 shrink-0" />
          <span>
            These slides are a saved copy — the original file is no longer on the server, so the
            open and download links below won&apos;t work until the deck is uploaded again.
          </span>
        </p>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            onClick={() => step(-1)}
            disabled={index === 0}
            className="w-9 h-9 rounded-lg border border-background-300 text-foreground-600 hover:bg-background-100 disabled:opacity-40 disabled:hover:bg-transparent"
            aria-label={`Previous ${unit.toLowerCase()}`}
          >
            <AppIcon className="ri-arrow-left-s-line text-lg" />
          </button>
          <button
            type="button"
            onClick={() => step(1)}
            disabled={index >= slideCount - 1}
            className="w-9 h-9 rounded-lg border border-background-300 text-foreground-600 hover:bg-background-100 disabled:opacity-40 disabled:hover:bg-transparent"
            aria-label={`Next ${unit.toLowerCase()}`}
          >
            <AppIcon className="ri-arrow-right-s-line text-lg" />
          </button>
        </div>
        <p className="text-xs font-semibold text-foreground-600" aria-live="polite">
          {unit} {slide.number} of {slideCount}
        </p>
        {slide.notes && (
          <button
            type="button"
            onClick={() => setShowNotes(value => !value)}
            className="ml-auto inline-flex items-center gap-1.5 text-xs font-semibold text-orange-600 hover:text-orange-700"
          >
            <AppIcon className="ri-sticky-note-line" />
            {showNotes ? 'Hide speaker notes' : 'Speaker notes'}
          </button>
        )}
      </div>

      {slide.notes && showNotes && (
        <p className="mt-2 whitespace-pre-wrap rounded-lg border border-background-200 bg-background-50 p-3 text-xs leading-relaxed text-foreground-600">
          {slide.notes}
        </p>
      )}

    </div>
  );
}

/** One slide or page, drawn at its real size and scaled to fit the frame.
 *
 * The frame keeps the document's aspect ratio but is capped to the window, so a
 * portrait A4 page on a wide card does not become taller than the screen — the
 * reader would land in the middle of a page whose top they cannot see. Because
 * the cap can make the frame shorter than the ratio asks for, the scale fits
 * BOTH dimensions and the stage is centred in whatever room is left. */
function SlideStage({ slide, widthPx, heightPx, onReady }: { slide: Slide; widthPx: number; heightPx: number; onReady?: () => void }) {
  const frameRef = useRef<HTMLDivElement | null>(null);
  const [scale, setScale] = useState(0);

  useEffect(() => {
    if (!onReady || !scale) return;
    const images = Array.from(frameRef.current?.querySelectorAll('img') ?? []);
    // PDF pages may also be supplied as a CSS background image.
    if (slide.background?.image) {
      const background = new Image();
      background.src = slide.background.image;
      images.push(background);
    }
    const loaded = new Set(images.filter(image => image.complete && image.naturalWidth > 0));
    const check = () => { if (loaded.size === images.length) onReady(); };
    const onLoad = (event: Event) => { loaded.add(event.target as HTMLImageElement); check(); };
    images.forEach(image => image.addEventListener('load', onLoad));
    check();
    return () => images.forEach(image => image.removeEventListener('load', onLoad));
  }, [slide, onReady, scale]);

  useLayoutEffect(() => {
    const frame = frameRef.current;
    if (!frame) return;
    const measure = () => {
      const { clientWidth, clientHeight } = frame;
      if (!clientWidth || !clientHeight) return;
      setScale(Math.min(clientWidth / widthPx, clientHeight / heightPx));
    };
    measure();
    // ResizeObserver is absent in jsdom, so tests fall back to the one measure.
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(measure);
    observer.observe(frame);
    // The cap is in viewport units, so a window resize changes the frame even
    // when the card's own width has not moved.
    window.addEventListener('resize', measure);
    return () => {
      observer.disconnect();
      window.removeEventListener('resize', measure);
    };
  }, [widthPx, heightPx]);

  const background = slide.background ?? {};
  return (
    <div
      ref={frameRef}
      className="relative flex w-full items-center justify-center overflow-hidden rounded-xl border border-background-300"
      style={{
        aspectRatio: `${widthPx} / ${heightPx}`,
        maxHeight: 'calc(100vh - 14rem)',
        backgroundColor: background.color || '#ffffff',
        backgroundImage: background.image ? `url(${background.image})` : undefined,
        backgroundSize: 'cover',
        backgroundPosition: 'center',
      }}
    >
      <div
        style={{
          position: 'relative',
          width: widthPx,
          height: heightPx,
          flex: 'none',
          transform: `scale(${scale})`,
          // Centred rather than top-left, so the leftover room from fitting the
          // shorter dimension is split evenly.
          transformOrigin: 'center',
          // Until the frame has been measured the stage would flash at full
          // size, overflowing the card.
          visibility: scale ? 'visible' : 'hidden',
        }}
      >
        {slide.shapes.map((shape, shapeIndex) => (
          <ShapeView key={shapeIndex} shape={shape} />
        ))}
      </div>
    </div>
  );
}

function ShapeView({ shape }: { shape: SlideShape }) {
  const box: React.CSSProperties = {
    position: 'absolute',
    left: shape.x,
    top: shape.y,
    width: shape.w,
    height: shape.h,
  };

  if (shape.kind === 'image') {
    return (
      <img
        src={shape.src}
        alt={shape.alt}
        style={{
          ...box,
          objectFit: 'contain',
          transform: shape.rotation ? `rotate(${shape.rotation}deg)` : undefined,
        }}
      />
    );
  }

  if (shape.kind === 'note') {
    return (
      <div
        style={{ ...box, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
        className="rounded-lg border border-dashed border-foreground-300 bg-background-50/70"
      >
        <span className="text-sm font-semibold text-foreground-400">{shape.label}</span>
      </div>
    );
  }

  if (shape.kind === 'table') return <TableView shape={shape} box={box} />;

  const justify = shape.valign === 'middle' ? 'center' : shape.valign === 'bottom' ? 'flex-end' : 'flex-start';
  const framed = Boolean(shape.fill || shape.line);
  return (
    <div
      style={{
        ...box,
        // PowerPoint lets text spill past a plain text box, and the sizes here
        // are approximations, so clipping would cut real words off. A filled or
        // outlined shape has a visible edge, and text must stay inside it.
        overflow: framed ? 'hidden' : 'visible',
        color: shape.defaultTextColor,
        // A freeform shape draws its own outline below, so the box stays bare.
        backgroundColor: shape.path ? undefined : shape.fill || undefined,
        borderRadius: shape.radius || undefined,
        border: !shape.path && shape.line ? `${shape.lineWidthPx || 1}px solid ${shape.line}` : undefined,
        transform: shape.rotation ? `rotate(${shape.rotation}deg)` : undefined,
      }}
    >
      {shape.path && (
        // Stretched to the shape's box, exactly as PowerPoint scales the path
        // it stored in its own coordinate space.
        <svg
          viewBox={`0 0 ${shape.path.vbW} ${shape.path.vbH}`}
          preserveAspectRatio="none"
          aria-hidden="true"
          style={{ position: 'absolute', inset: 0, width: '100%', height: '100%' }}
        >
          <path
            d={shape.path.d}
            fill={shape.fill || 'none'}
            stroke={shape.line || 'none'}
            strokeWidth={shape.line ? (shape.lineWidthPx || 1) * (shape.path.vbW / (shape.w || 1)) : undefined}
          />
        </svg>
      )}
      {/* Positioned so the text paints above the outline, not under it. */}
      <div
        style={{
          position: 'relative',
          height: '100%',
          display: 'flex',
          flexDirection: 'column',
          justifyContent: justify,
        }}
      >
        {shape.paragraphs.map((paragraph, paragraphIndex) => (
          <ParagraphView key={paragraphIndex} paragraph={paragraph} />
        ))}
      </div>
    </div>
  );
}

function TableView({ shape, box }: { shape: Extract<SlideShape, { kind: 'table' }>; box: React.CSSProperties }) {
  return (
    <table
      style={{
        ...box,
        borderCollapse: 'collapse',
        tableLayout: 'fixed',
      }}
    >
      <colgroup>
        {shape.colFractions.map((fraction, columnIndex) => (
          <col key={columnIndex} style={{ width: `${fraction * 100}%` }} />
        ))}
      </colgroup>
      <tbody>
        {shape.rows.map((row, rowIndex) => (
          <tr key={rowIndex} style={{ height: (shape.rowFractions[rowIndex] ?? 0) * shape.h }}>
            {row.map((cell, cellIndex) => (
              <CellView key={cellIndex} cell={cell} />
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function CellView({ cell }: { cell: SlideTableCell }) {
  return (
    <td
      colSpan={cell.colSpan > 1 ? cell.colSpan : undefined}
      rowSpan={cell.rowSpan > 1 ? cell.rowSpan : undefined}
      style={{
        backgroundColor: cell.fill || undefined,
        verticalAlign: cell.valign === 'middle' ? 'middle' : cell.valign === 'bottom' ? 'bottom' : 'top',
        padding: '4px 8px',
        overflow: 'hidden',
      }}
    >
      {cell.paragraphs.map((paragraph, paragraphIndex) => (
        <ParagraphView key={paragraphIndex} paragraph={paragraph} />
      ))}
    </td>
  );
}

function ParagraphView({ paragraph }: { paragraph: SlideParagraph }) {
  const runs = paragraph.runs.map((run, runIndex) => (
    <span
      key={runIndex}
      style={{
        fontSize: run.sizePx,
        fontWeight: run.bold ? 700 : 400,
        fontStyle: run.italic ? 'italic' : undefined,
        textDecoration: run.underline ? 'underline' : undefined,
        color: run.color || undefined,
        // The deck's own font if the reader happens to have it, then the app's.
        fontFamily: run.font ? `'${run.font}', var(--font-sans, sans-serif)` : undefined,
        whiteSpace: 'pre-wrap',
      }}
    >
      {run.text}
    </span>
  ));
  const style: React.CSSProperties = {
    margin: 0,
    textAlign: paragraph.align || undefined,
    lineHeight: paragraph.lineHeightPx ? `${paragraph.lineHeightPx}px` : (paragraph.lineSpacing || 1.25),
    paddingLeft: paragraph.level * INDENT_PX,
  };

  if (!paragraph.bullet) return <p style={style}>{runs}</p>;
  // A bulleted paragraph hangs its text off the glyph, so wrapped lines line up
  // with the first line instead of sliding back under the bullet.
  return (
    <p style={{ ...style, display: 'flex', gap: '0.5em', textAlign: undefined }}>
      <span aria-hidden="true" style={{ fontSize: paragraph.runs[0]?.sizePx, lineHeight: 'inherit' }}>
        {paragraph.bullet}
      </span>
      <span style={{ flex: 1, textAlign: paragraph.align || undefined }}>{runs}</span>
    </p>
  );
}
