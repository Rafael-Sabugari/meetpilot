import type { CaptureStatus, SourceStatus } from "@meetpilot/shared-types";

export type IndicatorTone = "idle" | "live" | "paused" | "warning" | "error";

export interface Indicator {
  tone: IndicatorTone;
  /** i18n key */
  labelKey:
    | "indicator.idle"
    | "indicator.live"
    | "indicator.paused"
    | "indicator.reconnecting"
    | "indicator.partial"
    | "indicator.failed";
}

/**
 * The capture indicator must never understate what is happening:
 * if any source is (re)connecting or live, the indicator shows capture.
 */
export function indicatorFor(status: CaptureStatus | null): Indicator {
  if (!status || status.state === "idle") return { tone: "idle", labelKey: "indicator.idle" };
  if (status.state === "paused") return { tone: "paused", labelKey: "indicator.paused" };

  const live = status.sources.filter((s) => s.state === "capturing");
  const reconnecting = status.sources.some((s) => s.state === "reconnecting");
  const failed = status.sources.filter((s) => s.state === "failed");

  if (reconnecting) return { tone: "warning", labelKey: "indicator.reconnecting" };
  if (live.length > 0 && failed.length > 0) return { tone: "warning", labelKey: "indicator.partial" };
  if (live.length > 0) return { tone: "live", labelKey: "indicator.live" };
  if (failed.length === status.sources.length && failed.length > 0)
    return { tone: "error", labelKey: "indicator.failed" };
  return { tone: "live", labelKey: "indicator.live" };
}

/** Map dBFS (-60..0) to 0..100 for meters. */
export function dbfsToPercent(db: number): number {
  if (!Number.isFinite(db)) return 0;
  const clamped = Math.max(-60, Math.min(0, db));
  return Math.round(((clamped + 60) / 60) * 100);
}

/** No level updates for this long means the source is silent (e.g. nothing playing). */
export const LEVEL_STALE_MS = 1200;

export function isLevelStale(lastUpdateMs: number | undefined, nowMs: number): boolean {
  return lastUpdateMs === undefined || nowMs - lastUpdateMs > LEVEL_STALE_MS;
}

export function formatDuration(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const mm = String(m).padStart(2, "0");
  const ss = String(s).padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

export function sourceProblem(s: SourceStatus): string | null {
  if (s.state === "failed") {
    const e = s.error;
    switch (e.kind) {
      case "permission_denied":
        return e.hint;
      case "unsupported":
        return e.reason;
      case "device_unavailable":
      case "backend":
        return e.detail;
      case "device_not_found":
        return "device_not_found";
      default:
        return null;
    }
  }
  return null;
}
