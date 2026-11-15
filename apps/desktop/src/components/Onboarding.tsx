import type { MessageKey } from "../lib/i18n";

export function Onboarding({ t, onDone }: { t: (k: MessageKey) => string; onDone: () => void }) {
  return (
    <section className="card onboarding">
      <h2>{t("onboarding.title")}</h2>
      <p className="onboarding__lead">{t("onboarding.noBot")}</p>
      <ul>
        <li>{t("onboarding.what")}</li>
        <li>{t("onboarding.where")}</li>
        <li>{t("onboarding.control")}</li>
        <li>{t("onboarding.consent")}</li>
      </ul>
      <button className="btn btn--primary" onClick={onDone}>
        {t("onboarding.continue")}
      </button>
    </section>
  );
}
