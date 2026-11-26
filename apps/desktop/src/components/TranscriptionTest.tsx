import { useEffect, useRef, useState } from "react";
import { listen } from "@tauri-apps/api/event";
import type { MessageKey } from "../lib/i18n";
import { api, isTauri, toCommandError, type BenchResult } from "../lib/api";

type T = (k: MessageKey) => string;

/** About 20 seconds read aloud, with the kind of terms said in real meetings. */
export const REFERENCE: Record<string, string> = {
  "pt-BR":
    "Na reunião de ontem, a gente combinou migrar os dados de clientes para o Salesforce. " +
    "A integração com o SAP vai usar Platform Events e uma API REST. " +
    "Antes de subir para produção, precisamos de um sandbox com dados mascarados e testes com duzentos registros. " +
    "O próximo passo é revisar o modelo de dados com o arquiteto na quinta-feira.",
  en:
    "In yesterday's meeting we agreed to migrate the customer data to Salesforce. " +
    "The integration with SAP will use Platform Events and a REST API. " +
    "Before going to production we need a sandbox with masked data and tests with two hundred records. " +
    "The next step is to review the data model with the architect on Thursday.",
};

const MAX_SECONDS = 28;

/** Read a known text aloud; every on-device engine transcribes the same recording and we
 * show how many words each got wrong. The recording exists only in memory for the test. */
export function TranscriptionTest(props: { t: T; language: string; model: string | null; disabled: boolean }) {
  const { t, language, model, disabled } = props;
  const reference = REFERENCE[language] ?? REFERENCE["pt-BR"];
  const [phase, setPhase] = useState<"idle" | "recording" | "comparing">("idle");
  const [elapsed, setElapsed] = useState(0);
  const [level, setLevel] = useState(-100);
  const [result, setResult] = useState<BenchResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const started = useRef(0);

  useEffect(() => {
    if (phase !== "recording") return;
    const id = window.setInterval(() => setElapsed(Math.floor((Date.now() - started.current) / 1000)), 250);
    const off = isTauri() ? listen<{ rms_dbfs: number }>("bench://level", (e) => setLevel(e.payload.rms_dbfs)) : null;
    return () => {
      window.clearInterval(id);
      void off?.then((f) => f());
    };
  }, [phase]);

  const finish = async () => {
    setPhase("comparing");
    try {
      setResult(await api.benchmarkFinish(reference, language, model));
    } catch (e) {
      setError(toCommandError(e).message);
    } finally {
      setPhase("idle");
    }
  };
  useEffect(() => {
    if (phase === "recording" && elapsed >= MAX_SECONDS) void finish();
  }, [elapsed, phase]);

  const start = async () => {
    setError(null);
    setResult(null);
    try {
      await api.benchmarkStart();
      started.current = Date.now();
      setElapsed(0);
      setPhase("recording");
    } catch (e) {
      setError(toCommandError(e).message);
    }
  };

  const best = result?.results.filter((r) => r.wer !== null).sort((a, b) => (a.wer ?? 1) - (b.wer ?? 1))[0];
  return (
    <details className="bench" data-testid="transcription-test">
      <summary>{t("bench.title")}</summary>
      <p className="muted small">{t("bench.help")}</p>
      <blockquote className="bench__text">{reference}</blockquote>
      {phase === "recording" ? (
        <div className="row">
          <span className="rec-dot" aria-hidden="true" />
          <strong>{t("bench.recording")}</strong>
          <span className="muted small">
            {elapsed}s / {MAX_SECONDS}s · {level > -60 ? "🔊" : "…"}
          </span>
          <button className="btn btn--primary btn--small" onClick={() => void finish()}>
            {t("bench.finish")}
          </button>
          <button
            className="btn btn--small"
            onClick={() => {
              void api.benchmarkCancel();
              setPhase("idle");
            }}
          >
            {t("bench.cancel")}
          </button>
        </div>
      ) : (
        <button className="btn btn--small" disabled={disabled || phase === "comparing"} onClick={() => void start()}>
          {phase === "comparing" ? t("bench.comparing") : `🎙 ${t("bench.start")}`}
        </button>
      )}
      {error && <p className="error-text small">{error}</p>}
      {result && (
        <table className="bench__table">
          <thead>
            <tr>
              <th>{t("bench.engine")}</th>
              <th>{t("bench.wer")}</th>
              <th>{t("bench.time")}</th>
            </tr>
          </thead>
          <tbody>
            {result.results.map((r) => (
              <tr key={r.engine} className={r === best ? "bench__best" : ""}>
                <td>
                  {r === best ? "🏆 " : ""}
                  {r.label}
                  <div className="muted small bench__heard">{r.error ? `⚠ ${r.error}` : r.text}</div>
                </td>
                <td>{r.wer === null ? "—" : `${Math.round(r.wer * 100)}%`}</td>
                <td>{r.seconds.toFixed(1)}s</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {result && <p className="muted small">{t("bench.privacy")}</p>}
    </details>
  );
}
