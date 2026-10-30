export type PairQuestionType = 'matching' | 'image_matching';

export interface ParsedPairAnswer {
  left: string;
  right: string;
  imageUrl: string;
  isImage: boolean;
}

interface ImageMatchingAnswerPayload {
  kind: 'image_matching_pair';
  imageUrl: string;
  label: string;
  match: string;
}

const IMAGE_MATCHING_KIND = 'image_matching_pair';
const IMAGE_EXTENSION_PATTERN = /\.(png|jpe?g|gif|webp|svg)(?:[?#].*)?$/i;

function splitTextPair(value: string) {
  const parts = String(value || '').split(/\s*(?:->|=>|=)\s*/);
  return {
    left: (parts[0] || '').trim(),
    right: (parts.length > 1 ? parts.slice(1).join(' -> ') : '').trim(),
  };
}

function parseImageMatchingPayload(value: string): ImageMatchingAnswerPayload | null {
  const trimmed = String(value || '').trim();
  if (!trimmed.startsWith('{')) return null;

  try {
    const payload = JSON.parse(trimmed);
    if (!payload || typeof payload !== 'object') return null;

    const imageUrl = typeof payload.imageUrl === 'string' ? payload.imageUrl.trim() : '';
    const label = typeof payload.label === 'string'
      ? payload.label.trim()
      : typeof payload.left === 'string'
        ? payload.left.trim()
        : '';
    const match = typeof payload.match === 'string'
      ? payload.match.trim()
      : typeof payload.right === 'string'
        ? payload.right.trim()
        : '';
    const kind = typeof payload.kind === 'string' ? payload.kind.trim() : '';

    if (!imageUrl && !label && !match) return null;
    if (kind && kind !== IMAGE_MATCHING_KIND) return null;

    return {
      kind: IMAGE_MATCHING_KIND,
      imageUrl,
      label,
      match,
    };
  } catch {
    return null;
  }
}

export function looksLikeImageSource(value: string) {
  const trimmed = String(value || '').trim();
  if (!trimmed) return false;
  if (trimmed.startsWith('data:image/')) return true;
  return /^https?:\/\//i.test(trimmed) && IMAGE_EXTENSION_PATTERN.test(trimmed);
}

export function isPairQuestionType(value: string): value is PairQuestionType {
  return value === 'matching' || value === 'image_matching';
}

export function parseQuizPairAnswer(value: string, type: PairQuestionType): ParsedPairAnswer {
  if (type === 'image_matching') {
    const payload = parseImageMatchingPayload(value);
    if (payload) {
      return {
        left: payload.label,
        right: payload.match,
        imageUrl: payload.imageUrl,
        isImage: Boolean(payload.imageUrl),
      };
    }
  }

  const pair = splitTextPair(value);
  const imageUrl = type === 'image_matching' && looksLikeImageSource(pair.left) ? pair.left : '';
  return {
    left: imageUrl ? '' : pair.left,
    right: pair.right,
    imageUrl,
    isImage: Boolean(imageUrl),
  };
}

export function serializeQuizPairAnswer(
  type: PairQuestionType,
  pair: Pick<ParsedPairAnswer, 'left' | 'right' | 'imageUrl'>,
) {
  const left = String(pair.left || '').trim();
  const right = String(pair.right || '').trim();
  const imageUrl = String(pair.imageUrl || '').trim();

  if (type === 'image_matching' && imageUrl) {
    return JSON.stringify({
      kind: IMAGE_MATCHING_KIND,
      imageUrl,
      label: left,
      match: right,
    });
  }

  return `${left} -> ${right}`;
}

export function isPairAnswerComplete(type: PairQuestionType, value: string) {
  const pair = parseQuizPairAnswer(value, type);
  if (type === 'image_matching') {
    return Boolean(pair.right.trim() && (pair.imageUrl.trim() || pair.left.trim()));
  }
  return Boolean(pair.left.trim() && pair.right.trim());
}

export function convertAnswerTextForQuestionType(value: string, fromType: string, toType: string) {
  if (fromType === toType) return value;

  if (isPairQuestionType(fromType) && isPairQuestionType(toType)) {
    return serializeQuizPairAnswer(toType, parseQuizPairAnswer(value, fromType));
  }

  if (isPairQuestionType(fromType) && !isPairQuestionType(toType)) {
    const pair = parseQuizPairAnswer(value, fromType);
    return pair.right || pair.left || '';
  }

  if (!isPairQuestionType(fromType) && isPairQuestionType(toType)) {
    return serializeQuizPairAnswer(toType, { left: String(value || '').trim(), right: '', imageUrl: '' });
  }

  return value;
}

export async function readImageFileAsDataUrl(file: File) {
  if (!file.type.startsWith('image/')) {
    throw new Error('Choose a valid image file.');
  }
  if (file.size > 5 * 1024 * 1024) {
    throw new Error('Use an image smaller than 5 MB.');
  }

  return await new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      if (typeof reader.result === 'string') {
        resolve(reader.result);
      } else {
        reject(new Error('Could not read this image.'));
      }
    };
    reader.onerror = () => reject(new Error('Could not read this image.'));
    reader.readAsDataURL(file);
  });
}
