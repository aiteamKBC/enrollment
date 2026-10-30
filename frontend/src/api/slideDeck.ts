// ============================================================================
// Slide-deck rendering API client.
// An uploaded PowerPoint has no inline preview of its own — Microsoft's Office
// Online viewer can only show a file it can download from the public internet.
// The backend reads the deck's OOXML instead and returns the geometry, text and
// images below, which SlideDeckViewer positions on a fixed-size stage.
// GETs /curriculum_api/curriculum/presentations/slides/?src=<upload path>.
// ============================================================================

import { createCachedResource } from './cachedRequest';
import { withCallerSignal } from './learnerRead';

const ENDPOINT = '/curriculum_api/curriculum/presentations/slides/';

export type SlideAlign = 'left' | 'center' | 'right' | 'justify';
export type SlideVerticalAlign = 'top' | 'middle' | 'bottom';

export interface SlideRun {
  text: string;
  sizePx: number;
  bold: boolean;
  italic: boolean;
  underline: boolean;
  color: string | null;
  font: string | null;
}

export interface SlideParagraph {
  align: SlideAlign | null;
  level: number;
  /** The glyph to show, or null for an unbulleted paragraph. */
  bullet: string | null;
  /** Line spacing as a multiple of the font size, when the deck gives one. */
  lineSpacing: number | null;
  /** Line spacing as an exact height in stage pixels; wins over lineSpacing. */
  lineHeightPx: number | null;
  runs: SlideRun[];
}

/** Position and size on the slide stage, in stage pixels. */
interface SlideBox { x: number; y: number; w: number; h: number }

interface SlideTextBody {
  valign: SlideVerticalAlign;
  paragraphs: SlideParagraph[];
}

export interface SlideTableCell extends SlideTextBody {
  colSpan: number;
  rowSpan: number;
  fill: string | null;
}

/** A shape's real outline, as SVG path data in its own coordinate space. */
export interface SlideOutline { d: string; vbW: number; vbH: number }

export type SlideShape =
  | (SlideBox & SlideTextBody & {
    kind: 'text' | 'shape';
    /** Custom (freeform) geometry — drawn as an SVG path instead of a box. */
    path: SlideOutline | null;
    /** CSS corner radius for a rounded preset shape (a pill, a circle). */
    radius: string | null;
    fill: string | null;
    line: string | null;
    lineWidthPx: number | null;
    rotation: number | null;
    /** Colour for runs that name none, picked for contrast with what they sit on. */
    defaultTextColor: string;
  })
  | (SlideBox & { kind: 'image'; src: string; alt: string; rotation: number | null })
  | (SlideBox & {
    kind: 'table';
    colFractions: number[];
    rowFractions: number[];
    rows: SlideTableCell[][];
  })
  /** Something the renderer cannot draw (a chart, SmartArt, an embedded object). */
  | (SlideBox & { kind: 'note'; label: string });

export interface Slide {
  number: number;
  layout: string;
  background: { color?: string; image?: string } | null;
  shapes: SlideShape[];
  notes: string;
}

export interface SlideDeck {
  slideWidthPx: number;
  slideHeightPx: number;
  slideCount: number;
  slides: Slide[];
  /** 'page' for a rendered PDF, absent (slides) for a deck. */
  unit?: 'slide' | 'page';
  /** True when the document was longer than the renderer's page cap. */
  truncated?: boolean;
  /** Pages the document actually has, when more than were rendered. */
  totalPages?: number;
  /** The uploaded file has gone; these slides come from the surviving render.
   *  Set by the backend — the open/download links stay broken until someone
   *  re-uploads the deck, so the viewer says so. */
  sourceMissing?: boolean;
}

/** The rendered slides for an uploaded deck.
 *
 * `src` is the deck's resource URL as authored (a site-relative upload path, or
 * an absolute URL on this origin). Rejects with the server's own message, which
 * is written to be shown to the learner — a missing file or a legacy .ppt both
 * come back as an explanation rather than a failure. */
async function loadSlideDeck(src: string): Promise<SlideDeck> {
  let response: Response;
  try {
    response = await fetch(`${ENDPOINT}?src=${encodeURIComponent(src)}`);
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new Error('The slides could not be loaded — the server could not be reached.');
  }
  const data = await response.json().catch(() => null) as (SlideDeck & { error?: string }) | null;
  if (!response.ok || !data) {
    throw new Error(data?.error || `The slides could not be loaded (${response.status}).`);
  }
  return data;
}

const decks = createCachedResource('slide-decks', loadSlideDeck, 30_000, 20);

export function fetchSlideDeck(src: string, signal?: AbortSignal): Promise<SlideDeck> {
  if (signal?.aborted) return Promise.reject(new DOMException('The operation was aborted.', 'AbortError'));
  return withCallerSignal(decks.read(src), signal);
}
