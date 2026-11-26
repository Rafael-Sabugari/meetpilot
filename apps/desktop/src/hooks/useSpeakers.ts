import { useCallback, useEffect, useState } from "react";
import { api, events, isTauri, toCommandError, type SpeakersStatus } from "../lib/api";

/** Speaker labels (free mode): is it in this build, is its small voice model downloaded? */
export function useSpeakers() {
  const [status, setStatus] = useState<SpeakersStatus | null>(null);
  const [downloading, setDownloading] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const refresh = useCallback(() => {
    if (!isTauri()) return;
    api.speakersStatus().then(setStatus).catch(() => setStatus(null));
  }, []);
  useEffect(() => {
    refresh();
    if (!isTauri()) return;
    const un = events.onLocalDownload((e) => {
      if (e.id !== status?.model_id && status) return;
      if (e.state === "progress") setDownloading(e.total ? Math.round((e.downloaded / e.total) * 100) : 0);
      else {
        setDownloading(null);
        if (e.state === "error") setError(e.message ?? "download failed");
        refresh();
      }
    });
    return () => void un.then((f) => f());
  }, [refresh, status]);
  const download = async () => {
    if (!status) return;
    setError(null);
    setDownloading(0);
    try {
      await api.localDownload(status.model_id);
    } catch (e) {
      setDownloading(null);
      setError(toCommandError(e).message);
    }
  };
  return { status, downloading, error, download };
}
