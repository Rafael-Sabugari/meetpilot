import { describe, expect, it } from "vitest";
import type { CaptureStatus, SourceStatus } from "@meetpilot/shared-types";
import { dbfsToPercent, formatDuration, indicatorFor, isLevelStale, sourceProblem } from "./indicator";

const base = {
  device_name: "Speakers",
  native_format: { sample_rate: 48000, channels: 2 },
  frames_emitted: 0,
  captured_ms: 0,
  dropped_chunks: 0,
  reconnects: 0,
};
const src = (source: "system" | "microphone", s: Partial<SourceStatus> & Pick<SourceStatus, "state">): SourceStatus =>
  ({ ...base, source, ...s }) as SourceStatus;
const status = (state: CaptureStatus["state"], sources: SourceStatus[]): CaptureStatus => ({ state, sources });

describe("indicatorFor", () => {
  it("is idle with no session", () => {
    expect(indicatorFor(null).tone).toBe("idle");
    expect(indicatorFor(status("idle", [])).tone).toBe("idle");
  });
  it("shows live while any source captures", () => {
    expect(indicatorFor(status("capturing", [src("system", { state: "capturing" })])).tone).toBe("live");
  });
  it("shows paused when the session is paused", () => {
    expect(indicatorFor(status("paused", [src("system", { state: "paused" })])).labelKey).toBe("indicator.paused");
  });
  it("never hides capture while reconnecting", () => {
    const i = indicatorFor(status("capturing", [src("system", { state: "reconnecting", attempt: 2 })]));
    expect(i.tone).toBe("warning");
    expect(i.labelKey).toBe("indicator.reconnecting");
  });
  it("warns on partial failure but still signals capture", () => {
    const i = indicatorFor(
      status("capturing", [
        src("system", { state: "capturing" }),
        src("microphone", { state: "failed", error: { kind: "device_not_found", source: "microphone" } }),
      ]),
    );
    expect(i.labelKey).toBe("indicator.partial");
  });
  it("is error when every source failed", () => {
    const i = indicatorFor(
      status("capturing", [src("system", { state: "failed", error: { kind: "backend", source: "system", detail: "x" } })]),
    );
    expect(i.tone).toBe("error");
  });
  it("treats starting sources as live (capture may begin any moment)", () => {
    expect(indicatorFor(status("capturing", [src("system", { state: "starting" })])).tone).toBe("live");
  });
});

describe("helpers", () => {
  it("maps dBFS to percent", () => {
    expect(dbfsToPercent(0)).toBe(100);
    expect(dbfsToPercent(-30)).toBe(50);
    expect(dbfsToPercent(-100)).toBe(0);
    expect(dbfsToPercent(Number.NaN)).toBe(0);
  });
  it("detects stale levels", () => {
    expect(isLevelStale(undefined, 1000)).toBe(true);
    expect(isLevelStale(1000, 1500)).toBe(false);
    expect(isLevelStale(1000, 3000)).toBe(true);
  });
  it("formats durations", () => {
    expect(formatDuration(0)).toBe("00:00");
    expect(formatDuration(65_000)).toBe("01:05");
    expect(formatDuration(3_725_000)).toBe("1:02:05");
  });
  it("extracts actionable problem text", () => {
    expect(
      sourceProblem(src("microphone", { state: "failed", error: { kind: "permission_denied", source: "microphone", hint: "Open Settings" } })),
    ).toBe("Open Settings");
    expect(sourceProblem(src("system", { state: "capturing" }))).toBeNull();
  });
});
