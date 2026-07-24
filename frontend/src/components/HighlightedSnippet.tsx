import { Fragment, type ElementType } from 'react';
import {
  normalizeHighlightRanges,
  parseLegacyHighlightedSnippet,
} from '../features/search/highlights';
import type { HighlightRange } from '../types/api';

type HighlightedSnippetProps = {
  snippet: string;
  highlights?: HighlightRange[] | null;
  className?: string;
  as?: ElementType;
};

export default function HighlightedSnippet({
  snippet,
  highlights,
  className,
  as: Wrapper = 'span',
}: HighlightedSnippetProps) {
  const parsed =
    highlights == null ? parseLegacyHighlightedSnippet(snippet) : { text: snippet, highlights: [] };
  const codePoints = Array.from(parsed.text);
  const ranges = normalizeHighlightRanges(
    [...parsed.highlights, ...(highlights ?? [])],
    codePoints.length
  );
  const children = [];
  let cursor = 0;

  for (const range of ranges) {
    if (range.start > cursor) children.push(codePoints.slice(cursor, range.start).join(''));
    children.push(
      <mark key={`${range.start}:${range.end}`}>
        {codePoints.slice(range.start, range.end).join('')}
      </mark>
    );
    cursor = range.end;
  }
  if (cursor < codePoints.length) children.push(codePoints.slice(cursor).join(''));

  return (
    <Wrapper className={className}>
      {children.map((child, index) => (
        <Fragment key={index}>{child}</Fragment>
      ))}
    </Wrapper>
  );
}
