import { describe, expect, it } from "vitest";
import type { RealtimeEnvelope, TranscriptSegment } from "@meetpilot/shared-types";
import { emptyTranscript, MAX_FINAL_SEGMENTS, recentLines, transcriptAsText, transcriptReducer, type TranscriptState } from "./transcript";

let seq = 0;
const env = (type: RealtimeEnvelope["type"], payload: unknown): RealtimeEnvelope => ({
  v: 1,
  type,
  meeting_id: "m1",
  seq: seq++,
  ts: "2026-11-15T00:00:00Z",
  payload,
});
const seg = (source: "system" | "microphone", text: string, is_final: boolean, start_ms = 0): TranscriptSegment => ({
  segment_id: `${source}-${start_ms}`,
  source,
  text,
  language: "pt-BR",
  start_ms,
  end_ms: start_ms + 1000,
  is_final,
  speaker: null,
});
const apply = (s: TranscriptState, e: RealtimeEnvelope) => transcriptReducer(s, { kind: "server", envelope: e });

describe("transcriptReducer", () => {
  it("shows a partial, then replaces it with the final", () => {
    let s = apply(emptyTranscript, env("transcript.partial", seg("system", "Bom", false)));
    expect(s.partials.system?.text).toBe("Bom");
    s = apply(s, env("transcript.final", seg("system", "Bom dia.", true)));
    expect(s.partials.system).toBeUndefined();
    expect(s.finals.map((f) => f.text)).toEqual(["Bom dia."]);
  });

  it("keeps partials per source independent", () => {
    let s = apply(emptyTranscript, env("transcript.partial", seg("system", "eles", false)));
    s = apply(s, env("transcript.partial", seg("microphone", "eu", false)));
    s = apply(s, env("transcript.final", seg("microphone", "eu falei", true)));
    expect(s.partials.system?.text).toBe("eles");
    expect(s.partials.microphone).toBeUndefined();
  });

  it("records provider, usage and errors", () => {
    let s = apply(emptyTranscript, env("capture.started", { provider: "deepgram", remaining_seconds: 3600 }));
    s = apply(s, env("usage.updated", { session_seconds: 10, remaining_seconds: 3590 }));
    s = apply(s, env("error", { code: "stt_error", message: "x", retryable: true }));
    expect(s.provider).toBe("deepgram");
    expect(s.remainingAtStart).toBe(3600);
    expect(s.usage?.remaining_seconds).toBe(3590);
    expect(s.errors[0].code).toBe("stt_error");
  });

  it("ignores unknown schema versions and empty finals", () => {
    const bad = { ...env("transcript.final", seg("system", "x", true)), v: 2 } as unknown as RealtimeEnvelope;
    expect(apply(emptyTranscript, bad)).toBe(emptyTranscript);
    expect(apply(emptyTranscript, env("transcript.final", seg("system", "  ", true))).finals).toHaveLength(0);
  });

  it("bounds memory", () => {
    let s = emptyTranscript;
    for (let i = 0; i < MAX_FINAL_SEGMENTS + 20; i++) s = apply(s, env("transcript.final", seg("system", `l${i}`, true, i)));
    expect(s.finals).toHaveLength(MAX_FINAL_SEGMENTS);
    expect(s.finals[0].text).toBe("l20");
  });

  it("tracks link status and resets", () => {
    let s = transcriptReducer(emptyTranscript, { kind: "link", status: { state: "reconnecting", attempt: 2, retry_in_ms: 1000 } });
    expect(s.link?.state).toBe("reconnecting");
    s = transcriptReducer(s, { kind: "reset" });
    expect(s).toBe(emptyTranscript);
  });
});

describe("helpers", () => {
  it("returns recent lines including live partials", () => {
    let s = apply(emptyTranscript, env("transcript.final", seg("system", "a", true)));
    s = apply(s, env("transcript.final", seg("system", "b", true)));
    s = apply(s, env("transcript.partial", seg("microphone", "c…", false)));
    expect(recentLines(s, 2)).toEqual([
      { text: "b", source: "system", final: true },
      { text: "c…", source: "microphone", final: false },
    ]);
  });

  it("exports plain text with labels and timestamps", () => {
    const s = apply(emptyTranscript, env("transcript.final", seg("microphone", "Olá", true, 65_000)));
    expect(transcriptAsText(s, { microphone: "Você" })).toBe("[01:05] Você: Olá");
  });
});

describe("reconnect safety", () => {
  it("keeps the transcript and link status when the server restarts its sequence after a reconnect", () => {
    let s = transcriptReducer(emptyTranscript, { kind: "link", status: { state: "connecting" } });
    s = transcriptReducer(s, { kind: "link", status: { state: "connected" } });
    s = apply(s, { ...env("capture.started", { provider: "fake" }), seq: 0 });
    s = apply(s, env("transcript.final", seg("system", "antes da queda", true)));
    s = transcriptReducer(s, { kind: "link", status: { state: "reconnecting", attempt: 1, retry_in_ms: 500 } });
    s = transcriptReducer(s, { kind: "link", status: { state: "connected" } });
    s = apply(s, { ...env("capture.started", { provider: "fake" }), seq: 0 });
    expect(s.finals.map((f) => f.text)).toEqual(["antes da queda"]);
    expect(s.link?.state).toBe("connected");
  });

  it("clears the previous meeting when a new capture starts connecting", () => {
    let s = apply(emptyTranscript, env("transcript.final", seg("system", "reunião anterior", true)));
    s = transcriptReducer(s, { kind: "link", status: { state: "connecting" } });
    expect(s.finals).toHaveLength(0);
    expect(s.link?.state).toBe("connecting");
  });
});

it("drops interim text when the link ends", () => {
  let s = apply(emptyTranscript, env("transcript.partial", seg("system", "meio da fra", false)));
  s = transcriptReducer(s, { kind: "link", status: { state: "failed", reason: "x", code: null } });
  expect(s.partials).toEqual({});
  expect(s.link?.state).toBe("failed");
});

describe("translation and detected questions", () => {
  it("attaches translations to their final line, in recent lines and in the export", () => {
    let s = apply(emptyTranscript, env("transcript.final", seg("system", "Bom dia a todos.", true, 0)));
    s = apply(s, env("translation.updated", { segment_id: "system-0", source: "system", target_language: "en", text: "Good morning, everyone." }));
    expect(s.translations["system-0"]).toBe("Good morning, everyone.");
    expect(recentLines(s, 2)[0]).toMatchObject({ text: "Bom dia a todos.", translation: "Good morning, everyone." });
    expect(transcriptAsText(s, { system: "Outros" })).toContain("→ Good morning, everyone.");
  });

  it("ignores empty translations and bounds them", () => {
    let s = apply(emptyTranscript, env("translation.updated", { segment_id: "x", source: "system", target_language: "en", text: " " }));
    expect(s.translations).toEqual({});
    for (let i = 0; i < MAX_FINAL_SEGMENTS + 10; i++)
      s = apply(s, env("translation.updated", { segment_id: `s-${i}`, source: "system", target_language: "en", text: "t" }));
    expect(Object.keys(s.translations)).toHaveLength(MAX_FINAL_SEGMENTS);
    expect(s.translations["s-0"]).toBeUndefined();
  });

  it("marks detected questions once and clears them for a new meeting", () => {
    let s = apply(emptyTranscript, env("question.detected", { question_id: "q-system-0", segment_id: "system-0", text: "?", directed: true }));
    s = apply(s, env("question.detected", { question_id: "q-system-0", segment_id: "system-0", text: "?", directed: true }));
    expect(s.questions).toEqual(["system-0"]);
    s = transcriptReducer(s, { kind: "link", status: { state: "connecting" } });
    expect(s.questions).toEqual([]);
    expect(s.translations).toEqual({});
  });
});
