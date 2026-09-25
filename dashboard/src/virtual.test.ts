import { describe, expect, it } from "vitest";

import { computeWindow, DEFAULT_ROW_HEIGHT } from "./virtual";

describe("row windowing", () => {
  it("renders the first screenful for a short list", () => {
    const window_ = computeWindow({
      rowCount: 10,
      scrollTop: 0,
      viewportHeight: 200,
      rowHeight: DEFAULT_ROW_HEIGHT,
    });
    expect(window_.startIndex).toBe(0);
    expect(window_.totalHeight).toBe(10 * DEFAULT_ROW_HEIGHT);
    expect(window_.paddingTop).toBe(0);
  });

  it("windows a 500 row list to a fraction of its height", () => {
    const window_ = computeWindow({
      rowCount: 500,
      scrollTop: 0,
      viewportHeight: 340,
      rowHeight: DEFAULT_ROW_HEIGHT,
      overscan: 6,
    });
    const rendered = window_.endIndex - window_.startIndex;
    expect(rendered).toBeLessThan(30);
    expect(rendered).toBeGreaterThan(10);
    expect(window_.paddingTop).toBe(0);
    expect(window_.paddingBottom).toBeGreaterThan(0);
  });

  it("moves the window as the list scrolls", () => {
    const top = computeWindow({ rowCount: 500, scrollTop: 0, viewportHeight: 340 });
    const scrolled = computeWindow({ rowCount: 500, scrollTop: 3400, viewportHeight: 340 });
    expect(scrolled.startIndex).toBeGreaterThan(top.startIndex);
    expect(scrolled.offsetY).toBe(scrolled.startIndex * DEFAULT_ROW_HEIGHT);
  });

  it("keeps the rendered span and the paddings equal to the total height", () => {
    const window_ = computeWindow({ rowCount: 500, scrollTop: 1234, viewportHeight: 300 });
    const renderedHeight = (window_.endIndex - window_.startIndex) * DEFAULT_ROW_HEIGHT;
    expect(window_.paddingTop + renderedHeight + window_.paddingBottom).toBe(window_.totalHeight);
  });

  it("overscans above the viewport so a scroll does not flash empty space", () => {
    const window_ = computeWindow({
      rowCount: 500,
      scrollTop: 3400,
      viewportHeight: 340,
      overscan: 6,
    });
    const firstVisible = Math.floor(3400 / DEFAULT_ROW_HEIGHT);
    expect(window_.startIndex).toBeLessThan(firstVisible);
  });

  it("handles an empty list", () => {
    const window_ = computeWindow({ rowCount: 0, scrollTop: 0, viewportHeight: 340 });
    expect(window_.endIndex).toBe(0);
    expect(window_.totalHeight).toBe(0);
  });

  it("handles a zero-height viewport without dividing by zero", () => {
    const window_ = computeWindow({ rowCount: 100, scrollTop: 0, viewportHeight: 0 });
    expect(window_.endIndex).toBe(0);
  });

  it("clamps a scroll position past the end of the list", () => {
    const window_ = computeWindow({ rowCount: 20, scrollTop: 99999, viewportHeight: 340 });
    expect(window_.endIndex).toBeLessThanOrEqual(20);
    expect(window_.paddingBottom).toBe(0);
  });

  it("never renders past the end of the list", () => {
    const window_ = computeWindow({ rowCount: 5, scrollTop: 0, viewportHeight: 2000 });
    expect(window_.endIndex).toBe(5);
  });
});
