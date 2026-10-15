"use client";

import { useCallback, useEffect, useRef } from "react";

type Target = () => HTMLElement | null | undefined;
type Move = { target: Target; origin: Element };

/**
 * Moves keyboard focus once the next render has committed, so the target can
 * be an element that render creates. For actions that disable or remove the
 * focused control, which would otherwise drop focus to the page (FE-09).
 *
 * `origin` is the element that had focus when the action started. Focus moves
 * only if it is still there or has fallen to the page, never away from where
 * the user has moved it while the action ran.
 */
export function useFocusAfterRender(): (target: Target, origin: Element) => void {
  const pending = useRef<Move | null>(null);
  useEffect(() => {
    const move = pending.current;
    if (!move) return;
    pending.current = null;
    const active = document.activeElement;
    if (active === move.origin || active === null || active === document.body) move.target()?.focus();
  });
  return useCallback((target: Target, origin: Element) => {
    pending.current = { target, origin };
  }, []);
}

/** The element with keyboard focus, if it is one of `elements` or inside one; otherwise null. */
export function focusIn(...elements: (Element | null | undefined)[]): Element | null {
  const active = document.activeElement;
  return active && elements.some((element) => element?.contains(active)) ? active : null;
}
