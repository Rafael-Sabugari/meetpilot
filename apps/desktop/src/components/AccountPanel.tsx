import { useState } from "react";
import type { MessageKey } from "../lib/i18n";
import { savedServer, type useAccount } from "../hooks/useAccount";

type T = (k: MessageKey) => string;

export function AccountPanel({ t, acct }: { t: T; acct: ReturnType<typeof useAccount> }) {
  const [server, setServer] = useState(savedServer);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [create, setCreate] = useState(false);

  if (acct.account) {
    const u = acct.usage;
    return (
      <section className="card">
        <h2>{t("account.title")}</h2>
        <p>
          {t("account.signedInAs")} <strong>{acct.account.email}</strong>{" "}
          <span className="pill pill--ok">{acct.account.plan}</span>
        </p>
        <p className="muted small">{acct.account.server}</p>
        {u && (
          <p className="small">
            {t("account.usage")}: {(u.used_seconds / 60).toFixed(1)} / {(u.limit_seconds / 60).toFixed(0)} {t("account.minutes")}
          </p>
        )}
        <button className="btn" onClick={() => { setCreate(false); acct.signOut(); }}>{t("account.signOut")}</button>
      </section>
    );
  }

  return (
    <section className="card">
      <h2>{t("account.title")}</h2>
      <form
        className="form"
        onSubmit={(e) => {
          e.preventDefault();
          acct.signIn({ server, email, password }, create).then((ok) => {
            if (ok) setPassword(""); // do not keep the password in the UI after sign-in
          });
        }}
      >
        <label>
          <span className="muted small">{t("account.server")}</span>
          <input value={server} onChange={(e) => setServer(e.target.value)} required />
        </label>
        <label>
          <span className="muted small">{t("account.email")}</span>
          <input type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </label>
        <label>
          <span className="muted small">{t("account.password")}</span>
          <input
            type="password"
            autoComplete={create ? "new-password" : "current-password"}
            value={password}
            minLength={create ? 10 : 1}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
          {create && <span className="muted small">{t("account.passwordHint")}</span>}
        </label>
        {acct.error && <p className="error-text">{acct.error}</p>}
        <div className="controls">
          <button className="btn btn--primary" type="submit" disabled={acct.busy}>
            {create ? t("account.create") : t("account.signIn")}
          </button>
          <button className="link" type="button" onClick={() => setCreate(!create)}>
            {create ? t("account.switchSignIn") : t("account.switchCreate")}
          </button>
        </div>
        <p className="muted small">{t("account.memoryOnly")}</p>
      </form>
    </section>
  );
}
