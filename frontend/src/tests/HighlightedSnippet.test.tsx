import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import HighlightedSnippet from '../components/HighlightedSnippet';

describe('HighlightedSnippet', () => {
  it('applies backend ranges using Unicode code-point offsets', () => {
    render(<HighlightedSnippet snippet="🚀 rent is high" highlights={[{ start: 2, end: 6 }]} />);

    expect(screen.getByText('rent', { selector: 'mark' })).toBeInTheDocument();
  });

  it('converts only exact legacy marker tags and renders all other markup literally', () => {
    const { container } = render(
      <HighlightedSnippet
        snippet={
          'safe <b>bold</b> <em>emphasis</em> <mark>rent</mark> <mark class="x">literal</mark> <img src=x onerror=alert(1)>'
        }
      />
    );

    expect(screen.getByText('rent', { selector: 'mark' })).toBeInTheDocument();
    expect(container.textContent).toBe(
      'safe bold emphasis rent <mark class="x">literal</mark> <img src=x onerror=alert(1)>'
    );
    expect([...container.querySelectorAll('mark')].map((node) => node.textContent)).toEqual([
      'bold',
      'emphasis',
      'rent',
    ]);
    expect(container.querySelector('img')).not.toBeInTheDocument();
    expect(container.querySelector('[class="x"]')).not.toBeInTheDocument();
  });

  it('sorts, clamps, and merges backend ranges before rendering', () => {
    const { container } = render(
      <HighlightedSnippet
        snippet="abcdefghij"
        highlights={[
          { start: 6, end: 9 },
          { start: -4, end: 2 },
          { start: 1, end: 4 },
          { start: 20, end: 30 },
          { start: 8, end: 12 },
          { start: 9, end: 8 },
          { start: Number.NaN, end: 4 },
          { start: 3, end: Number.POSITIVE_INFINITY },
        ]}
      />
    );

    expect([...container.querySelectorAll('mark')].map((node) => node.textContent)).toEqual([
      'abcd',
      'ghij',
    ]);
    expect(container.textContent).toBe('abcdefghij');
  });

  it('treats an explicit empty range list as plain text instead of legacy markup', () => {
    const { container } = render(
      <HighlightedSnippet snippet="literal <mark>text</mark>" highlights={[]} />
    );

    expect(container.textContent).toBe('literal <mark>text</mark>');
    expect(container.querySelector('mark')).not.toBeInTheDocument();
  });

  it('preserves malformed or unpaired legacy marker text literally', () => {
    const { container } = render(
      <HighlightedSnippet snippet="left <mark>open and close</em> right" />
    );

    expect(container.textContent).toBe('left <mark>open and close</em> right');
    expect(container.querySelector('mark')).not.toBeInTheDocument();
  });
});
