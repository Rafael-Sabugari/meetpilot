import { useCallback, useEffect, useReducer } from "react";
import type { CopilotMode, CopilotTone } from "@meetpilot/shared-types";
import { api, events, isTauri, toCommandError } from "../lib/api";
import { contextFromTranscript, copilotReducer, emptyCopilot, manualId } from "../lib/copilot";
import type { TranscriptState } from "../lib/transcript";

/**
 * Answers are broadcast by the app, so the main window and the overlay stay in sync. Manual
 * requests come from the copilot command; automatic suggestions from the realtime stream.
 */
export function useCopilot() {
  const [state, dispatch] = useReducer(copilotReducer, emptyCopilot);

  useEffect(() => {
    if (!isTauri()) return;
    const subs = [
      events.onCopilotPending((p) => {
        const id = manualId(p.id);
        if (p.state === "pending") dispatch({ kind: "pending", id, mode: p.mode, question: p.question });
        else dispatch({ kind: "failed", id, error: p.error ?? "error" });
      }),
      events.onCopilotAnswer((a) => {
        const { id, mode, question, ...answer } = a;
        dispatch({ kind: "answer", id: manualId(id), mode, question, answer });
      }),
      // Automatic suggestions arrive on the realtime stream (answer.started/completed/failed).
      events.onServer((envelope) => dispatch({ kind: "server", envelope })),
    ];
    return () => subs.forEach((p) => p.then((off) => off()));
  }, []);

  const ask = useCallback(
    async (
      mode: CopilotMode,
      question: string | null,
      tone: CopilotTone,
      transcript: TranscriptState,
      userContext: string | null = null,
    ): Promise<string | null> => {
      try {
        await api.askCopilot({
          mode,
          question,
          tone,
          transcript: contextFromTranscript(transcript),
          userContext,
        });
        return null;
      } catch (e) {
        // Errors before the request was sent (e.g. no meeting) have no pending item.
        return toCommandError(e).message;
      }
    },
    [],
  );

  return { state, ask, pending: state.items.some((i) => i.status === "pending") };
}
