/**
 * Windowing for large tables.
 *
 * At 500 robots a plain map-and-render of every row is enough to drop frames
 * while the map is animating, so the roster renders only the rows that can
 * actually be on screen. The maths is deliberately small and pure: it is the
 * kind of code that is easy to get subtly wrong and easy to test exactly.
 */

export interface Window {
  startIndex: number;
  endIndex: number;
  offsetY: number;
  totalHeight: number;
  paddingTop: number;
  paddingBottom: number;
}

export const DEFAULT_ROW_HEIGHT = 34;

/**
 * Compute the slice of rows to render for a scroll viewport.
 *
 * `overscan` keeps a few rows mounted above and below the viewport so a fast
 * scroll does not flash empty space before the next paint.
 */
export function computeWindow(options: {
  rowCount: number;
  scrollTop: number;
  viewportHeight: number;
  rowHeight?: number;
  overscan?: number;
}): Window {
  const {
    rowCount,
    scrollTop,
    viewportHeight,
    rowHeight = DEFAULT_ROW_HEIGHT,
    overscan = 6,
  } = options;

  const totalHeight = rowCount * rowHeight;
  if (rowCount === 0 || viewportHeight <= 0) {
    return {
      startIndex: 0,
      endIndex: 0,
      offsetY: 0,
      totalHeight: 0,
      paddingTop: 0,
      paddingBottom: 0,
    };
  }

  const safeTop = Math.max(0, Math.min(scrollTop, Math.max(0, totalHeight - 1)));
  const firstVisible = Math.floor(safeTop / rowHeight);
  const visibleCount = Math.ceil(viewportHeight / rowHeight) + 1;

  const startIndex = Math.max(0, firstVisible - overscan);
  const endIndex = Math.min(rowCount, firstVisible + visibleCount + overscan);
  const offsetY = startIndex * rowHeight;
  const renderedHeight = (endIndex - startIndex) * rowHeight;

  return {
    startIndex,
    endIndex,
    offsetY,
    totalHeight,
    paddingTop: offsetY,
    paddingBottom: Math.max(0, totalHeight - offsetY - renderedHeight),
  };
}
