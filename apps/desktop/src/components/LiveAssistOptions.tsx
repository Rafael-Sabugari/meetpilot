import type { AutoSuggestions, TranslateTo } from "@meetpilot/shared-types";
import type { MessageKey } from "../lib/i18n";
import type { LivePrefs } from "../lib/liveOptions";

type T = (k: MessageKey) => string;

const AUTO: AutoSuggestions[] = ["off", "directed", "all"];
const TARGETS: (TranslateTo | "none")[] = ["none", "pt-BR", "en"];

/** Automatic suggestions and translation. Can be changed before or during a meeting. */
export function LiveAssistOptions(props: {
  t: T;
  prefs: LivePrefs;
  onChange: (p: LivePrefs) => void;
  disabled: boolean;
}) {
  const { t, prefs, onChange, disabled } = props;
  return (
    <div className="live-options" data-testid="live-options">
      <label className="select">
        <span className="muted small">{t("live.auto")}</span>
        <select
          aria-label={t("live.auto")}
          value={prefs.autoSuggestions}
          disabled={disabled}
          onChange={(e) => onChange({ ...prefs, autoSuggestions: e.target.value as AutoSuggestions })}
        >
          {AUTO.map((a) => (
            <option key={a} value={a}>{t(`live.auto.${a}` as MessageKey)}</option>
          ))}
        </select>
      </label>
      <label className="select">
        <span className="muted small">{t("live.translate")}</span>
        <select
          aria-label={t("live.translate")}
          value={prefs.translateTo ?? "none"}
          disabled={disabled}
          onChange={(e) =>
            onChange({ ...prefs, translateTo: e.target.value === "none" ? null : (e.target.value as TranslateTo) })
          }
        >
          {TARGETS.map((x) => (
            <option key={x} value={x}>{t(`live.translate.${x}` as MessageKey)}</option>
          ))}
        </select>
      </label>
      {prefs.autoSuggestions !== "off" && <p className="muted small">{t("live.auto.hint")}</p>}
      <label className="check">
        <input
          type="checkbox"
          checked={prefs.remember}
          disabled={disabled}
          onChange={(e) => onChange({ ...prefs, remember: e.target.checked })}
          data-testid="remember-toggle"
        />
        {t("live.remember")}
      </label>
      <label className="check">
        <input
          type="checkbox"
          checked={prefs.useMemory}
          disabled={disabled}
          onChange={(e) => onChange({ ...prefs, useMemory: e.target.checked })}
          data-testid="memory-toggle"
        />
        {t("live.useMemory")}
      </label>
      {prefs.remember && <p className="muted small">{t("live.remember.hint")}</p>}
    </div>
  );
}
