/**
 * Draggable dividers.
 *
 * Three places in the console want a resizable boundary: the side column beside
 * the map, the dock along the bottom, and each column of a data table. All three
 * share this one component, because they share the same two hard problems -- a
 * pointer capture so a fast drag does not drop the gesture, and a clamp so a
 * panel cannot be dragged to nothing.
 *
 * The size is written straight to a CSS custom property on the target element
 * rather than to React state, so a drag never re-renders the rows underneath it.
 * That is the difference between a divider that feels solid and one that
 * stutters on a 500-row table.
 */

import { useCallback, useRef, useState } from "react";

export interface DividerProps {
  orientation: "vertical" | "horizontal";
  /** CSS length for the visible line. */
  className: string;
  /** Reads the live size at drag start, so a second drag continues from there. */
  current: () => number;
  /** Called with a clamped value on every move. */
  onResize: (value: number) => void;
  min: number;
  max: number;
  /**
   * `1` grows as the pointer moves right/down, `-1` shrinks. A divider on the
   * left edge of a column moves the opposite way to the pointer.
   */
  direction?: 1 | -1;
  label: string;
  /** Keyboard nudge per arrow press. */
  step?: number;
}

export function Divider(props: DividerProps) {
  const [active, setActive] = useState(false);
  const startRef = useRef({ pointer: 0, size: 0 });
  const propsRef = useRef(props);
  propsRef.current = props;

  const onPointerDown = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    const settings = propsRef.current;
    startRef.current = {
      pointer:
        settings.orientation === "vertical" ? event.clientX : event.clientY,
      size: settings.current(),
    };
    event.currentTarget.setPointerCapture(event.pointerId);
    setActive(true);
    event.preventDefault();
  }, []);

  const onPointerMove = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    const settings = propsRef.current;
    if (!event.currentTarget.hasPointerCapture(event.pointerId)) return;
    const now =
      settings.orientation === "vertical" ? event.clientX : event.clientY;
    const delta = (now - startRef.current.pointer) * (settings.direction ?? 1);
    const next = startRef.current.size + delta;
    settings.onResize(Math.max(settings.min, Math.min(settings.max, next)));
  }, []);

  const endDrag = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    setActive(false);
  }, []);

  const onKeyDown = useCallback((event: React.KeyboardEvent<HTMLDivElement>) => {
    const settings = propsRef.current;
    const grow = settings.orientation === "vertical" ? "ArrowRight" : "ArrowDown";
    const shrink = settings.orientation === "vertical" ? "ArrowLeft" : "ArrowUp";
    if (event.key !== grow && event.key !== shrink) return;
    const delta = (event.key === grow ? 1 : -1) * (settings.step ?? 16) * (settings.direction ?? 1);
    const next = settings.current() + delta;
    settings.onResize(Math.max(settings.min, Math.min(settings.max, next)));
    event.preventDefault();
  }, []);

  const className = active ? `${props.className} ${props.className}--active` : props.className;

  return (
    <div
      className={className}
      role="separator"
      tabIndex={0}
      aria-label={props.label}
      aria-orientation={props.orientation === "vertical" ? "vertical" : "horizontal"}
      aria-valuenow={Math.round(props.current())}
      aria-valuemin={props.min}
      aria-valuemax={props.max}
      title={`Drag to resize ${props.label.toLowerCase()}`}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
      onKeyDown={onKeyDown}
    />
  );
}
