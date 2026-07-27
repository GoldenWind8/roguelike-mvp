import { useLayoutEffect, useRef, type MouseEvent } from "react";


/**
 * Ignore the trailing pointer click that can land on a newly mounted modal
 * after the opening object consumed the first click of a double-click.
 * Keyboard and assistive-technology activation has detail 0 and stays live.
 */
export function usePointerSettle(
  activeKey: unknown,
  settleMilliseconds = 400,
) {
  const openedAtRef = useRef(Number.NEGATIVE_INFINITY);

  useLayoutEffect(() => {
    if (activeKey !== null && activeKey !== undefined) {
      openedAtRef.current = performance.now();
    }
  }, [activeKey]);

  return (event: MouseEvent<HTMLElement>) =>
    event.detail === 0
    || performance.now() - openedAtRef.current >= settleMilliseconds;
}
