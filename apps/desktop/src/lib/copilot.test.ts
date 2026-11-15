import { describe, expect, it } from "vitest";
import type { CopilotAnswer } from "@meetpilot/shared-types";
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
    let s = copilotReducer(emptyCopilot, { kind: "pending", id: 1, mode: "reply", question: null });
    expect(s.items[0].status).toBe("pending");
    s = copilotReducer(s, { kind: "answer", id: 1, mode: "reply", question: null, answer });
    expect(s.items).toHaveLength(1);
    expect(s.items[0].status).toBe("done");
    expect(s.items[0].answer?.answer).toBe("Podemos entregar na sexta.");
  });

  it("marks failures and keeps the newest first", () => {
    let s = copilotReducer(emptyCopilot, { kind: "pending", id: 1, mode: "ask", question: "a" });
    s = copilotReducer(s, { kind: "pending", id: 2, mode: "ask", question: "b" });
    s = copilotReducer(s, { kind: "failed", id: 1, error: "timeout" });
    expect(s.items.map((i) => i.id)).toEqual([2, 1]);
    expect(s.items[1]).toMatchObject({ status: "failed", error: "timeout" });
  });

  it("accepts answers whose pending event was missed (other window) and ignores duplicates", () => {
    let s = copilotReducer(emptyCopilot, { kind: "answer", id: 7, mode: "summarize", question: null, answer });
    s = copilotReducer(s, { kind: "pending", id: 7, mode: "summarize", question: null });
    expect(s.items).toHaveLength(1);
    expect(s.items[0].status).toBe("done");
  });

  it("is bounded", () => {
    let s = emptyCopilot;
    for (let i = 0; i < MAX_ITEMS + 5; i++) s = copilotReducer(s, { kind: "answer", id: i, mode: "ask", question: "q", answer });
    expect(s.items).toHaveLength(MAX_ITEMS);
    expect(s.items[0].id).toBe(MAX_ITEMS + 4);
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
