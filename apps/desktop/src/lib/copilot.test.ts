import { describe, expect, it } from "vitest";
import type { CopilotAnswer, RealtimeEnvelope } from "@meetpilot/shared-types";
import { contextFromTranscript, copilotReducer, emptyCopilot, MAX_ITEMS } from "./copilot";
import { emptyTranscript, type TranscriptState } from "./transcript";

const answer: CopilotAnswer = {
  answer: "Podemos entregar na sexta.",
  grounding: "from_conversation",
  explanation: "",
  missing_info: [],
  provider: "openai",
  requests_remaining: 10,
};

describe("copilotReducer", () => {
  it("shows a pending item that becomes the answer", () => {
    let s = copilotReducer(emptyCopilot, { kind: "pending", id: "m-1", mode: "reply", question: null });
    expect(s.items[0].status).toBe("pending");
    s = copilotReducer(s, { kind: "answer", id: "m-1", mode: "reply", question: null, answer });
    expect(s.items).toHaveLength(1);
    expect(s.items[0].status).toBe("done");
    expect(s.items[0].answer?.answer).toBe("Podemos entregar na sexta.");
  });

  it("marks failures and keeps the newest first", () => {
    let s = copilotReducer(emptyCopilot, { kind: "pending", id: "m-1", mode: "ask", question: "a" });
    s = copilotReducer(s, { kind: "pending", id: "m-2", mode: "ask", question: "b" });
    s = copilotReducer(s, { kind: "failed", id: "m-1", error: "timeout" });
    expect(s.items.map((i) => i.id)).toEqual(["m-2", "m-1"]);
    expect(s.items[1]).toMatchObject({ status: "failed", error: "timeout" });
  });

  it("accepts answers whose pending event was missed (other window) and ignores duplicates", () => {
    let s = copilotReducer(emptyCopilot, { kind: "answer", id: "m-7", mode: "summarize", question: null, answer });
    s = copilotReducer(s, { kind: "pending", id: "m-7", mode: "summarize", question: null });
    expect(s.items).toHaveLength(1);
    expect(s.items[0].status).toBe("done");
  });

  it("is bounded", () => {
    let s = emptyCopilot;
    for (let i = 0; i < MAX_ITEMS + 5; i++) s = copilotReducer(s, { kind: "answer", id: `m-${i}`, mode: "ask", question: "q", answer });
    expect(s.items).toHaveLength(MAX_ITEMS);
    expect(s.items[0].id).toBe(`m-${MAX_ITEMS + 4}`);
  });
});

const env = (type: RealtimeEnvelope["type"], payload: unknown, seq = 1): RealtimeEnvelope => ({
  v: 1,
  type,
  meeting_id: "m",
  seq,
  ts: "2026-11-15T00:00:00Z",
  payload,
});

describe("automatic suggestions from the realtime stream", () => {
  const q = "Qual é a sua experiência com Kubernetes?";

  it("shows a pending auto item that becomes the answer", () => {
    let s = copilotReducer(emptyCopilot, { kind: "server", envelope: env("answer.started", { question_id: "q-system-1", question: q }) });
    expect(s.items[0]).toMatchObject({ id: "q-system-1", mode: "auto", question: q, status: "pending" });
    s = copilotReducer(s, {
      kind: "server",
      envelope: env("answer.completed", { question_id: "q-system-1", question: q, ...answer }, 2),
    });
    expect(s.items).toHaveLength(1);
    expect(s.items[0]).toMatchObject({ mode: "auto", status: "done", question: q });
    expect(s.items[0].answer?.grounding).toBe("from_conversation");
    expect(s.items[0].answer).not.toHaveProperty("question_id");
  });

  it("shows failures even without a pending item (quota)", () => {
    const s = copilotReducer(emptyCopilot, {
      kind: "server",
      envelope: env("answer.failed", { question_id: "q-x", code: "copilot_quota", message: "Limite atingido", retryable: false }),
    });
    expect(s.items[0]).toMatchObject({ id: "q-x", status: "failed", error: "Limite atingido", mode: "auto" });
  });

  it("does not mix manual and automatic ids and ignores other events", () => {
    let s = copilotReducer(emptyCopilot, { kind: "pending", id: "m-1", mode: "reply", question: null });
    s = copilotReducer(s, { kind: "server", envelope: env("answer.started", { question_id: "q-1", question: q }) });
    s = copilotReducer(s, { kind: "server", envelope: env("transcript.final", { text: "x" }) });
    expect(s.items.map((i) => i.id)).toEqual(["q-1", "m-1"]);
    const unknown = { ...env("answer.started", { question_id: "q-2", question: q }), v: 2 } as unknown as RealtimeEnvelope;
    expect(copilotReducer(s, { kind: "server", envelope: unknown })).toBe(s);
  });
});

describe("contextFromTranscript", () => {
  it("includes finals and live partials in time order", () => {
    const t: TranscriptState = {
      ...emptyTranscript,
      finals: [
        { segment_id: "a", source: "system", text: "Bom dia.", language: "pt-BR", start_ms: 0, end_ms: 900, is_final: true, speaker: null },
        { segment_id: "b", source: "microphone", text: "Oi!", language: "pt-BR", start_ms: 1000, end_ms: 1400, is_final: true, speaker: null },
      ],
      partials: {
        system: { segment_id: "c", source: "system", text: "Qual é o prazo do", language: "pt-BR", start_ms: 3000, end_ms: 3800, is_final: false, speaker: null },
        microphone: { segment_id: "d", source: "microphone", text: "  ", language: "pt-BR", start_ms: 2000, end_ms: 2100, is_final: false, speaker: null },
      },
    };
    expect(contextFromTranscript(t)).toEqual([
      { source: "system", text: "Bom dia.", start_ms: 0 },
      { source: "microphone", text: "Oi!", start_ms: 1000 },
      { source: "system", text: "Qual é o prazo do", start_ms: 3000 },
    ]);
  });
});
