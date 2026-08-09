import { useCallback, useEffect, useRef, useState } from 'react';

/** Returns `value` delayed by `delay` ms, resetting the timer on every change. */
export function useDebouncedValue<T>(value: T, delay = 300): T {
  const [debounced, setDebounced] = useState(value);

  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(id);
  }, [value, delay]);

  return debounced;
}

/**
 * The element's current width in pixels, 0 until it is measured.
 *
 * For layout that has to reason in both units at once: a chart placing bars by
 * percentage still owes each one a minimum number of pixels, and only the real
 * width converts between the two.
 */
export function useElementWidth<T extends HTMLElement>(): [(node: T | null) => void, number] {
  const [width, setWidth] = useState(0);
  const observer = useRef<ResizeObserver | null>(null);

  // A callback ref, not a ref + effect: StrictMode runs effects twice, and an
  // effect cleanup that disconnects would leave the observer dead for the rest
  // of the page's life — every track measuring 0 and every bar falling back to
  // its floor. React calls this with null on unmount, which is the teardown.
  const ref = useCallback((node: T | null) => {
    observer.current?.disconnect();
    observer.current = null;
    if (!node) return;
    setWidth(node.getBoundingClientRect().width);
    observer.current = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.current.observe(node);
  }, []);

  return [ref, width];
}

/** Tracks the OS `prefers-color-scheme: dark` media query. */
export function usePrefersDark(): boolean {
  const [dark, setDark] = useState(
    () =>
      typeof window !== 'undefined' && window.matchMedia('(prefers-color-scheme: dark)').matches,
  );

  useEffect(() => {
    const mq = window.matchMedia('(prefers-color-scheme: dark)');
    const handler = (e: MediaQueryListEvent) => setDark(e.matches);
    mq.addEventListener('change', handler);
    return () => mq.removeEventListener('change', handler);
  }, []);

  return dark;
}
