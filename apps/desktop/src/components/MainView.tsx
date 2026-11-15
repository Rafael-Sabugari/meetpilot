import { useEffect, useMemo, useState } from "react";
import type { AudioSource, DeviceInfo, PermissionState, SupportLevel } from "@meetpilot/shared-types";
import { api, isTauri, toCommandError, type CapabilitiesDto } from "../lib/api";
import { formatDuration, isLevelStale, sourceProblem } from "../lib/indicator";
import type { MessageKey } from "../lib/i18n";
import { useCapture } from "../hooks/useCapture";
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

  const onStart = () =>
    cap.start({
      systemAudio: useSystem ? { device_id: systemDevice || null } : null,
      microphone: useMic ? { device_id: micDevice || null } : null,
      demo,
    });

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
          </fieldset>

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
                <button className="btn btn--danger" disabled={cap.busy} onClick={cap.stop}>■ {t("controls.stop")}</button>
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
