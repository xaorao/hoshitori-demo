import { useEffect } from "react";

/** When a page is opened on one of its items (a receipt links to a Desk run or a practice book), move to it and give it the focus, so a
 * keyboard or screen-reader user lands where the link pointed; the item carries ``tabIndex={-1}`` to take the focus. Nothing when the
 * address names no item, or one the page does not have. */
export function useOpenOn(elementId: string | null): void {
  useEffect(() => {
    if (elementId === null) return;
    const target = document.getElementById(elementId);
    if (target === null) return;
    target.scrollIntoView({ block: "start" });
    target.focus({ preventScroll: true });
  }, [elementId]);
}
