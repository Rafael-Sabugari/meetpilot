import { describe, expect, it, afterEach } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { CaptureIndicator } from "./CaptureIndicator";
import { makeT } from "../lib/i18n";

const t = makeT("en");
afterEach(cleanup);

describe("CaptureIndicator", () => {
  it("announces live capture accessibly", () => {
    render(
      <CaptureIndicator
        t={t}
        demo={false}
        status={{ state: "capturing", sources: [{ source: "system", state: "capturing", device_name: null, native_format: null, frames_emitted: 0, captured_ms: 0, dropped_chunks: 0, reconnects: 0 }] }}
      />,
    );
    const el = screen.getByRole("status");
    expect(el.textContent).toContain("Capturing audio");
    expect(el.className).toContain("indicator--live");
  });
  it("labels demo mode so a generated tone is never mistaken for real audio", () => {
    render(<CaptureIndicator t={t} demo status={{ state: "capturing", sources: [] }} />);
    expect(screen.getByText("DEMO")).toBeTruthy();
  });
});
