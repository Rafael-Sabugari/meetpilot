import type { CaptureStatus } from "@meetpilot/shared-types";
import { indicatorFor } from "../lib/indicator";
import type { MessageKey } from "../lib/i18n";

interface Props {
  status: CaptureStatus | null;
  demo: boolean;
  t: (k: MessageKey) => string;
  compact?: boolean;
}

export function CaptureIndicator({ status, demo, t, compact }: Props) {
  const ind = indicatorFor(status);
  return (
    <div
      className={`indicator indicator--${ind.tone}${compact ? " indicator--compact" : ""}`}
      role="status"
      aria-live="polite"
      data-testid="capture-indicator"
    >
      <span className="indicator__dot" aria-hidden />
      <span className="indicator__label" title={t(ind.labelKey)}>{t(ind.labelKey)}</span>
      {demo && status?.state !== "idle" && <span className="badge">DEMO</span>}
    </div>
  );
}
