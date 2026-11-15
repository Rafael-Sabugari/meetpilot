import { useEffect, useMemo, useState } from "react";
import type { AudioSource, DeviceInfo, Language, PermissionState, SupportLevel } from "@meetpilot/shared-types";
import { api, isTauri, toCommandError, type CapabilitiesDto, type StatusDto } from "../lib/api";
import { formatDuration, isLevelStale, sourceProblem } from "../lib/indicator";
import type { MessageKey } from "../lib/i18n";
import { useCapture } from "../hooks/useCapture";
import { useAccount } from "../hooks/useAccount";
import { useTranscript } from "../hooks/useTranscript";
import { useCopilot } from "../hooks/useCopilot";
import { CopilotPanel } from "./CopilotPanel";
import { LiveAssistOptions } from "./LiveAssistOptions";
import { UserContextCard } from "./UserContextCard";
import { loadLivePrefs, saveLivePrefs, toLiveOptions, type LivePrefs } from "../lib/liveOptions";
import { AccountPanel } from "./AccountPanel";
import { ConsentDialog, hasTranscriptionConsent } from "./ConsentDialog";
import { TranscriptPanel } from "./TranscriptPanel";
import { CaptureIndicator } from "./CaptureIndicator";
import { LevelMeter } from "./LevelMeter";
import { Onboarding } from "./Onboarding";

type T = (k: MessageKey) => string;
const ONBOARDED_KEY = "meetpilot.onboarded.v1";
const isMac = typeof navigator !== "undefined" && /Mac/i.test(navigator.platform);
const MOD = isMac ? "⌘⇧" : "Ctrl+Shift+";

function readOnboarded(): boolean {
  try {
    return localStorage.getItem(ONBOARDED_KEY) === "1";
  } catch {
    return false;
  }
}

export function MainView({ t }: { t: T }) {
  const cap = useCapture();
  const [onboarded, setOnboarded] = useState(readOnboarded);
  const [caps, setCaps] = useState<CapabilitiesDto | null>(null);
  const [devices, setDevices] = useState<{ inputs: DeviceInfo[]; outputs: DeviceInfo[] }>({ inputs: [], outputs: [] });
  const [useSystem, setUseSystem] = useState(true);
  const [useMic, setUseMic] = useState(false);
  const [systemDevice, setSystemDevice] = useState<string>("");
  const [micDevice, setMicDevice] = useState<string>("");
  const [demo, setDemo] = useState(false);
  const [protect, setProtect] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);
  const acct = useAccount();
  const transcript = useTranscript();
  const copilot = useCopilot();
  const [transcribe, setTranscribe] = useState(false);
  const [language, setLanguage] = useState<Language>(() =>
    navigator.language?.toLowerCase().startsWith("pt") ? "pt-BR" : "en",
  );
  const [showConsent, setShowConsent] = useState(false);
  const [live, setLive] = useState<LivePrefs>(loadLivePrefs);

  const nativeAvailable = !!caps?.capabilities;

  const refresh = () => {
    if (!isTauri()) return;
    api.capabilities().then((c) => {
      setCaps(c);
      if (!c.capabilities) setDemo(true);
    });
    api
      .devices()
      .then(setDevices)
      .catch((e) => setLocalError(toCommandError(e).message));
  };
  useEffect(refresh, []);

  const state = cap.status?.state ?? "idle";
  const idle = state === "idle";

  const canTranscribe = !!acct.account;
  const onStart = () => {
    return cap.start({
      systemAudio: useSystem ? { device_id: systemDevice || null } : null,
      microphone: useMic ? { device_id: micDevice || null } : null,
      demo,
      transcription: transcribe && canTranscribe ? { language, live: toLiveOptions(live) } : null,
    });
  };
  const onStop = async () => {
    await cap.stop();
    if (acct.account) acct.refreshUsage();
  };
  const toggleTranscribe = (on: boolean) => {
    if (on && !hasTranscriptionConsent()) {
      setShowConsent(true);
      return;
    }
    setTranscribe(on);
  };
  const droppedFrames = (cap.status as StatusDto | null)?.dropped_frames ?? 0;
  const meetingId = (cap.status as StatusDto | null)?.meeting_id ?? null;
  const transcribing = !!(cap.status as StatusDto | null)?.transcribing;
  const updateLive = (next: LivePrefs) => {
    const serverChanged =
      next.autoSuggestions !== live.autoSuggestions ||
      next.tone !== live.tone ||
      next.translateTo !== live.translateTo ||
      next.userContext !== live.userContext;
    setLive(next);
    saveLivePrefs(next);
    // Mid-meeting changes apply immediately; otherwise they are sent when capture starts.
    if (serverChanged && transcribing) {
      api.setLiveOptions(toLiveOptions(next)).catch((e) => setLocalError(`${t("live.applyError")}: ${toCommandError(e).message}`));
    }
  };
  // Live meeting, or the last one (the app keeps its id so you can ask after Stop).
  const copilotEnabled = !!acct.account && (!!meetingId || transcript.state.finals.length > 0);

  const requestPermission = async (source: AudioSource) => {
    try {
      await api.requestPermission(source);
    } catch (e) {
      setLocalError(toCommandError(e).message);
    }
    refresh();
  };

  const toggleProtect = async (enabled: boolean) => {
    try {
      setProtect(await api.setOverlayProtection(enabled));
    } catch (e) {
      setLocalError(toCommandError(e).message);
    }
  };

  const sources = useMemo(() => cap.status?.sources ?? [], [cap.status]);

  if (!onboarded) {
    return (
      <main className="shell">
        <Header t={t} cap={cap} />
        <Onboarding
          t={t}
          onDone={() => {
            try {
              localStorage.setItem(ONBOARDED_KEY, "1");
            } catch {
              /* storage unavailable: show onboarding again next time */
            }
            setOnboarded(true);
          }}
        />
      </main>
    );
  }

  return (
    <main className="shell">
      <Header t={t} cap={cap} />
      {!isTauri() && <p className="notice">Open this UI through the MeetPilot desktop app (npm run tauri dev).</p>}

      <div className="grid">
        <section className="card">
          <h2>{t("caps.title")}</h2>
          {caps && !nativeAvailable && <p className="notice">{t("caps.native_unavailable")}</p>}
          {caps?.capabilities && (
            <>
              <p className="muted">
                {caps.capabilities.os} {caps.capabilities.os_version ?? ""} · {caps.backend}
              </p>
              <CapRow t={t} label={t("caps.system")} support={caps.capabilities.system_audio}
                permission={caps.capabilities.system_audio_permission}
                onRequest={() => requestPermission("system")} />
              <CapRow t={t} label={t("caps.mic")} support={caps.capabilities.microphone}
                permission={caps.capabilities.microphone_permission}
                onRequest={() => requestPermission("microphone")} />
              <ul className="notes">
                {caps.capabilities.notes.map((n) => <li key={n}>{n}</li>)}
              </ul>
            </>
          )}
        </section>

        <section className="card">
          <h2>{t("sources.title")}</h2>
          <fieldset disabled={!idle || cap.busy} className="sources">
            <label className="check">
              <input type="checkbox" checked={useSystem} onChange={(e) => setUseSystem(e.target.checked)} />
              {t("sources.system")}
            </label>
            <DeviceSelect t={t} devices={devices.outputs} value={systemDevice} onChange={setSystemDevice} disabled={!useSystem || demo} />
            <label className="check">
              <input type="checkbox" checked={useMic} onChange={(e) => setUseMic(e.target.checked)} />
              {t("sources.mic")}
            </label>
            <DeviceSelect t={t} devices={devices.inputs} value={micDevice} onChange={setMicDevice} disabled={!useMic || demo} />
            <label className="check check--demo">
              <input type="checkbox" checked={demo} onChange={(e) => setDemo(e.target.checked)} disabled={!nativeAvailable} />
              {t("sources.demo")}
            </label>
            <div className="tx-options">
              <label className="check">
                <input
                  type="checkbox"
                  checked={transcribe && canTranscribe}
                  disabled={!canTranscribe}
                  onChange={(e) => toggleTranscribe(e.target.checked)}
                  data-testid="transcribe-toggle"
                />
                {t("tx.enable")}
              </label>
              {!canTranscribe && <span className="muted small">{t("tx.needAccount")}</span>}
              <label className="select">
                <span className="muted small">{t("tx.language")}</span>
                <select value={language} disabled={!transcribe || !canTranscribe} onChange={(e) => setLanguage(e.target.value as Language)}>
                  {(["pt-BR", "en", "multi"] as const).map((l) => (
                    <option key={l} value={l}>{t(`tx.lang.${l}` as MessageKey)}</option>
                  ))}
                </select>
              </label>
            </div>
          </fieldset>
          {/* Outside the fieldset: these can be changed during a meeting. */}
          <LiveAssistOptions t={t} prefs={live} onChange={updateLive} disabled={!transcribe || !canTranscribe} />

          <div className="controls">
            {idle ? (
              <button className="btn btn--primary" disabled={cap.busy || (!useSystem && !useMic)} onClick={onStart}>
                ● {t("controls.start")}
              </button>
            ) : (
              <>
                {state === "capturing" ? (
                  <button className="btn" disabled={cap.busy} onClick={cap.pause}>❚❚ {t("controls.pause")}</button>
                ) : (
                  <button className="btn" disabled={cap.busy} onClick={cap.resume}>▶ {t("controls.resume")}</button>
                )}
                <button className="btn btn--danger" disabled={cap.busy} onClick={onStop}>■ {t("controls.stop")}</button>
              </>
            )}
          </div>
        </section>

        <section className="card card--wide">
          <h2>{t("status.title")}</h2>
          {sources.length === 0 && <p className="muted">—</p>}
          {sources.map((s) => {
            const lvl = cap.levels[s.source];
            const stale = isLevelStale(lvl?.at, cap.now);
            const live = s.state === "capturing";
            const problem = sourceProblem(s);
            return (
              <div key={s.source} className="source">
                <LevelMeter
                  label={s.source === "system" ? t("caps.system") : t("caps.mic")}
                  db={live && !stale && lvl ? lvl.reading.rms_dbfs : null}
                  silentLabel={t("status.silent")}
                  active={live}
                />
                <p className="muted source__meta">
                  <strong className={`state state--${s.state}`}>
                    {s.state}{s.state === "reconnecting" ? ` #${s.attempt}` : ""}
                  </strong>
                  {" · "}{s.device_name ?? "—"}
                  {s.native_format && ` · ${t("status.native")} ${s.native_format.sample_rate} Hz/${s.native_format.channels}ch → 16 kHz mono`}
                  {" · "}{formatDuration(s.captured_ms)}
                  {" · "}{s.frames_emitted} {t("status.frames")}
                  {" · "}{s.dropped_chunks} {t("status.dropped")}
                  {" · "}{s.reconnects} {t("status.reconnects")}
                </p>
                {problem && (
                  <p className="error-text">
                    {problem === "device_not_found" ? t("errors.device_not_found") : problem}
                  </p>
                )}
              </div>
            );
          })}
        </section>

        {(transcribe || transcript.state.finals.length > 0 || transcript.state.link) && (
          <TranscriptPanel
            t={t}
            state={transcript.state}
            droppedFrames={droppedFrames}
            view={live.translationView}
            onViewChange={(translationView) => updateLive({ ...live, translationView })}
          />
        )}

        <UserContextCard t={t} value={live.userContext} onSave={(userContext) => updateLive({ ...live, userContext })} />

        {acct.account && (
          <CopilotPanel
            t={t}
            copilot={copilot}
            transcript={transcript.state}
            enabled={copilotEnabled}
            tone={live.tone}
            onToneChange={(tone) => updateLive({ ...live, tone })}
            userContext={live.userContext}
          />
        )}

        <AccountPanel t={t} acct={acct} />

        <section className="card">
          <h2>Overlay</h2>
          <button className="btn" onClick={() => api.setOverlayShown(true)}>{t("overlay.show")}</button>
          <label className="check">
            <input type="checkbox" checked={protect} onChange={(e) => toggleProtect(e.target.checked)} />
            {t("overlay.protect")}
          </label>
          <p className="muted small">{t("overlay.protect.note")}</p>
          <h3>{t("shortcuts.title")}</h3>
          <p className="muted small">
            <kbd>{MOD}M</kbd> {t("shortcuts.pause")} · <kbd>{MOD}O</kbd> {t("shortcuts.overlay")}
          </p>
        </section>

        {(cap.errors.length > 0 || localError) && (
          <section className="card card--errors">
            <h2>{t("errors.title")}</h2>
            {localError && <p className="error-text">{localError}</p>}
            {cap.errors.map((e) => (
              <p key={e.id} className="error-text">
                {e.message} <button className="link" onClick={() => cap.dismissError(e.id)}>×</button>
              </p>
            ))}
          </section>
        )}
      </div>
      {showConsent && (
        <ConsentDialog
          t={t}
          onAccept={() => {
            setShowConsent(false);
            setTranscribe(true);
          }}
          onCancel={() => setShowConsent(false)}
        />
      )}
      <footer className="muted small">{t("privacy.footer")}</footer>
    </main>
  );
}

function Header({ t, cap }: { t: T; cap: ReturnType<typeof useCapture> }) {
  return (
    <header className="header">
      <div>
        <h1>MeetPilot</h1>
        <p className="muted">{t("app.tagline")}</p>
      </div>
      <CaptureIndicator status={cap.status} demo={cap.demo} t={t} />
    </header>
  );
}

function CapRow(props: { t: T; label: string; support: SupportLevel; permission: PermissionState; onRequest: () => void }) {
  const { t, label, support, permission, onRequest } = props;
  const askable = permission === "not_determined" || permission === "denied";
  return (
    <div className="caprow">
      <span className="caprow__label">{label}</span>
      {support.status === "supported" ? (
        <span className="pill pill--ok">{support.backend}</span>
      ) : (
        <>
          <span className="pill pill--bad">unsupported</span>
          <p className="caprow__reason">{support.reason}</p>
        </>
      )}
      <span className="muted small">
        {t("caps.permission")}: {t(`perm.${permission}` as MessageKey)}
      </span>
      {askable && <button className="link" onClick={onRequest}>{t("caps.request")}</button>}
    </div>
  );
}

function DeviceSelect(props: { t: T; devices: DeviceInfo[]; value: string; onChange: (v: string) => void; disabled: boolean }) {
  const { t, devices, value, onChange, disabled } = props;
  return (
    <label className="select">
      <span className="muted small">{t("sources.device")}</span>
      <select value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
        <option value="">{t("sources.default")}</option>
        {devices.map((d) => (
          <option key={d.id} value={d.id}>
            {d.name}{d.is_default ? " ★" : ""}
          </option>
        ))}
      </select>
    </label>
  );
}
