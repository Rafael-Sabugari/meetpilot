import { useEffect, useState } from "react";
import { api, isTauri, type AppleStatus } from "../lib/api";

/** Whether Apple's on-device transcription can run here (checked only when asked for). */
export function useAppleSpeech(language: string | null): AppleStatus | null {
  const [status, setStatus] = useState<AppleStatus | null>(null);
  useEffect(() => {
    if (!language || !isTauri()) return;
    let alive = true;
    api
      .appleSpeechStatus(language)
      .then((s) => alive && setStatus(s))
      .catch(() => alive && setStatus({ available: false, reason: null }));
    return () => {
      alive = false;
    };
  }, [language]);
  return status;
}
