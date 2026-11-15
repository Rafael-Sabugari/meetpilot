import { useCallback, useEffect, useRef, useState } from "react";
import type { AudioSource, CaptureStatus, LevelReading } from "@meetpilot/shared-types";
import { api, events, isTauri, toCommandError, type StartRequest } from "../lib/api";

export interface LevelState {
  reading: LevelReading;
  at: number;
}

export interface UiError {
  id: number;
  message: string;
}

let errorSeq = 0;

export function useCapture() {
  const [status, setStatus] = useState<CaptureStatus | null>(null);
  const [demo, setDemo] = useState(false);
  const [levels, setLevels] = useState<Partial<Record<AudioSource, LevelState>>>({});
  const [errors, setErrors] = useState<UiError[]>([]);
  const [busy, setBusy] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const mounted = useRef(true);

  const pushError = useCallback((message: string) => {
    setErrors((prev) => [{ id: ++errorSeq, message }, ...prev].slice(0, 8));
  }, []);

  useEffect(() => {
    mounted.current = true;
    if (!isTauri()) return;
    const unlisten: Array<Promise<() => void>> = [
      events.onStatus((s) => setStatus(s)),
      events.onLevel((l) => setLevels((prev) => ({ ...prev, [l.source]: { reading: l, at: Date.now() } }))),
      events.onError((e) => pushError(toCommandError({ kind: e.kind, message: describe(e) }).message)),
    ];
    api
      .status()
      .then((s) => {
        setStatus(s);
        setDemo(s.demo);
      })
      .catch((e) => pushError(toCommandError(e).message));
    return () => {
      mounted.current = false;
      unlisten.forEach((p) => p.then((fn) => fn()));
    };
  }, [pushError]);

  // Refresh counters and the "silent" state while a session is active.
  const active = status !== null && status.state !== "idle";
  useEffect(() => {
    if (!active || !isTauri()) return;
    const id = window.setInterval(() => {
      setNow(Date.now());
      api
        .status()
        .then((s) => {
          if (!mounted.current) return;
          setStatus(s);
          setDemo(s.demo);
        })
        .catch(() => undefined);
    }, 500);
    return () => window.clearInterval(id);
  }, [active]);

  const run = useCallback(
    async (fn: () => Promise<CaptureStatus & { demo?: boolean }>) => {
      setBusy(true);
      try {
        const s = await fn();
        setStatus(s);
        if (typeof s.demo === "boolean") setDemo(s.demo);
      } catch (e) {
        pushError(toCommandError(e).message);
      } finally {
        setBusy(false);
      }
    },
    [pushError],
  );

  return {
    status,
    demo,
    levels,
    errors,
    busy,
    now,
    dismissError: (id: number) => setErrors((prev) => prev.filter((e) => e.id !== id)),
    start: (req: StartRequest) => {
      setLevels({});
      return run(() => api.start(req));
    },
    pause: () => run(api.pause),
    resume: () => run(api.resume),
    stop: () => run(api.stop),
  };
}

function describe(e: { kind: string } & Record<string, unknown>): string {
  for (const k of ["hint", "detail", "reason"]) {
    if (typeof e[k] === "string") return `${e.source ?? ""} ${e[k]}`.trim();
  }
  return e.kind;
}
