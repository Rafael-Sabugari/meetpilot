import { useCallback, useEffect, useState } from "react";
import { api, events, isTauri, toCommandError, type DownloadEvent, type LocalModels } from "../lib/api";

export interface LocalModelsState {
  data: LocalModels | null;
  /** Current download, if any. */
  progress: DownloadEvent | null;
  error: string | null;
  download: (id: string) => Promise<void>;
  cancel: (id: string) => Promise<void>;
}

/** Whisper models on this computer (free transcription) and their downloads. */
export function useLocalModels(): LocalModelsState {
  const [data, setData] = useState<LocalModels | null>(null);
  const [progress, setProgress] = useState<DownloadEvent | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    if (!isTauri()) return;
    api.localModels().then(setData).catch(() => setData({ available: false, models: [] }));
  }, []);

  useEffect(() => {
    refresh();
    if (!isTauri()) return;
    const un = events.onLocalDownload((e) => {
      if (e.state === "progress") setProgress(e);
      else {
        setProgress(null);
        if (e.state === "error") setError(e.message ?? "download failed");
        refresh();
      }
    });
    return () => {
      un.then((f) => f());
    };
  }, [refresh]);

  const download = useCallback(async (id: string) => {
    setError(null);
    setProgress({ id, state: "progress", downloaded: 0, total: null });
    try {
      await api.localDownload(id);
    } catch (e) {
      setProgress(null);
      setError(toCommandError(e).message);
    }
  }, []);

  const cancel = useCallback(async (id: string) => {
    await api.localCancelDownload(id).catch(() => {});
  }, []);

  return { data, progress, error, download, cancel };
}
