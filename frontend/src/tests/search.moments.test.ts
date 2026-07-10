import { describe, expect, it } from 'vitest';
import { plainTextFromSnippet } from '../features/search/moments';

describe('search moment text', () => {
  it('preserves angle-bracket transcript text while removing paired legacy markers', () => {
    expect(plainTextFromSnippet('  Use <T> with <mark>rent</mark> and 5 < 7  ')).toBe(
      'Use <T> with rent and 5 < 7'
    );
  });

  it('preserves exact marker-shaped text for new plain-text payloads', () => {
    expect(plainTextFromSnippet('literal <mark>text</mark>', [])).toBe('literal <mark>text</mark>');
  });
});
