import { describe, expect, it } from "vitest";
import { shouldDrag } from "./overlayDrag";

describe("overlay dragging", () => {
  const root = document.createElement("main");
  root.innerHTML = `<div class="grip">⠿</div><p id="line">texto</p><button id="stop"><span id="icon">■</span></button><select id="s"></select>`;
  document.body.appendChild(root);
  const q = (sel: string) => root.querySelector(sel);

  it("drags from empty space, the grip and text", () => {
    expect(shouldDrag(root, 0)).toBe(true);
    expect(shouldDrag(q(".grip"), 0)).toBe(true);
    expect(shouldDrag(q("#line"), 0)).toBe(true);
  });

  it("never steals clicks from controls, even on their inner elements", () => {
    expect(shouldDrag(q("#stop"), 0)).toBe(false);
    expect(shouldDrag(q("#icon"), 0)).toBe(false);
    expect(shouldDrag(q("#s"), 0)).toBe(false);
  });

  it("only with the main button", () => {
    expect(shouldDrag(root, 2)).toBe(false);
    expect(shouldDrag(null, 0)).toBe(false);
  });
});
