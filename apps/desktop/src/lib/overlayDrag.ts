import { getCurrentWindow } from "@tauri-apps/api/window";
import type { MouseEvent } from "react";

const INTERACTIVE = "button, a, input, select, textarea, label, [contenteditable], [data-no-drag]";

/** Dragging starts anywhere in the overlay except on controls (and not when selecting text). */
export function shouldDrag(target: EventTarget | null, button: number): boolean {
  if (button !== 0 || !(target instanceof Element)) return false;
  return !target.closest(INTERACTIVE);
}

export function startOverlayDrag(e: MouseEvent): void {
  if (!shouldDrag(e.target, e.button)) return;
  e.preventDefault();
  getCurrentWindow()
    .startDragging()
    .catch(() => {});
}
