import type { HighlightRange } from '../../types/api';

type MarkerToken = {
  start: number;
  end: number;
  tag: string;
  closing: boolean;
  pair?: MarkerToken;
  outputStart?: number;
};

export function parseLegacyHighlightedSnippet(snippet: string) {
  const markerPattern = /<\/?(b|em|mark)>/gi;
  const tokens: MarkerToken[] = [];
  const stack: MarkerToken[] = [];

  for (const match of snippet.matchAll(markerPattern)) {
    const token: MarkerToken = {
      start: match.index,
      end: match.index + match[0].length,
      tag: match[1].toLowerCase(),
      closing: match[0][1] === '/',
    };
    tokens.push(token);

    if (!token.closing) {
      stack.push(token);
    } else if (stack.at(-1)?.tag === token.tag) {
      const opening = stack.pop()!;
      opening.pair = token;
      token.pair = opening;
    }
  }

  let sourceCursor = 0;
  let outputLength = 0;
  let text = '';
  const highlights: HighlightRange[] = [];

  const append = (value: string) => {
    text += value;
    outputLength += Array.from(value).length;
  };

  for (const token of tokens) {
    append(snippet.slice(sourceCursor, token.start));
    if (!token.pair) {
      append(snippet.slice(token.start, token.end));
    } else if (!token.closing) {
      token.outputStart = outputLength;
    } else {
      highlights.push({ start: token.pair.outputStart ?? outputLength, end: outputLength });
    }
    sourceCursor = token.end;
  }
  append(snippet.slice(sourceCursor));

  return { text, highlights };
}

export function normalizeHighlightRanges(
  ranges: HighlightRange[],
  codePointLength: number
): HighlightRange[] {
  const normalized = ranges
    .filter(({ start, end }) => Number.isFinite(start) && Number.isFinite(end))
    .map(({ start, end }) => ({
      start: Math.max(0, Math.min(codePointLength, Math.trunc(start))),
      end: Math.max(0, Math.min(codePointLength, Math.trunc(end))),
    }))
    .filter(({ start, end }) => end > start)
    .sort((left, right) => left.start - right.start || left.end - right.end);

  return normalized.reduce<HighlightRange[]>((merged, range) => {
    const previous = merged.at(-1);
    if (!previous || range.start > previous.end) {
      merged.push({ ...range });
    } else {
      previous.end = Math.max(previous.end, range.end);
    }
    return merged;
  }, []);
}
