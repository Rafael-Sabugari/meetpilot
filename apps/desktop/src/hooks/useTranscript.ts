import { useEffect, useReducer } from "react";
import { events, isTauri } from "../lib/api";
import { emptyTranscript, transcriptReducer } from "../lib/transcript";

export function useTranscript() {
  const [state, dispatch] = useReducer(transcriptReducer, emptyTranscript);
  useEffect(() => {
    if (!isTauri()) return;
    const subs = [
      events.onServer((envelope) => dispatch({ kind: "server", envelope })),
      events.onLink((status) => dispatch({ kind: "link", status })),
    ];
    return () => subs.forEach((p) => p.then((off) => off()));
  }, []);
  return { state, reset: () => dispatch({ kind: "reset" }) };
}
