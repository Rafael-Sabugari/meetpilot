import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { linkLabel, TranscriptPanel } from "./TranscriptPanel";
import { emptyTranscript, type TranscriptState } from "../lib/transcript";
import { makeT } from "../lib/i18n";

const t = makeT("pt");
afterEach(cleanup);

const state: TranscriptState = {
  ...emptyTranscript,
  provider: "fake",
  link: { state: "connected" },
  finals: [
    { segment_id: "s1", source: "system", text: "Qual é o prazo?", language: "pt-BR", start_ms: 5000, end_ms: 6000, is_final: true, speaker: null },
  ],
  partials: {
    microphone: { segment_id: "m1", source: "microphone", text: "Sexta…", language: "pt-BR", start_ms: 7000, end_ms: 7500, is_final: false, speaker: null },
  },
};

describe("TranscriptPanel", () => {
  it("labels speakers by source and marks partial text", () => {
    render(<TranscriptPanel t={t} state={state} droppedFrames={0} />);
    expect(screen.getByText("Qual é o prazo?").closest(".line")?.textContent).toContain("Outros");
    const partial = screen.getByText("Sexta…").parentElement!;
    expect(partial.className).toContain("line--partial");
    expect(partial.textContent).toContain("Você");
  });

  it("warns when the server uses the placeholder provider and when audio was dropped", () => {
    render(<TranscriptPanel t={t} state={state} droppedFrames={12} />);
    expect(screen.getByText(/texto de exemplo/)).toBeTruthy();
    expect(screen.getByText(/12 quadros de áudio/)).toBeTruthy();
  });
});

describe("translation and detected questions", () => {
  const translated: TranscriptState = {
    ...state,
    partials: {},
    translations: { s1: "What is the deadline?" },
    questions: ["s1"],
  };

  it("shows original and translation by default and marks the question", () => {
    render(<TranscriptPanel t={t} state={translated} droppedFrames={0} />);
    const line = screen.getByText("Qual é o prazo?").closest(".line")!;
    expect(line.className).toContain("line--question");
    expect(line.textContent).toContain("Pergunta");
    expect(screen.getByTestId("translation").textContent).toBe("What is the deadline?");
  });

  it("can show only the translation or only the original", () => {
    const { rerender } = render(<TranscriptPanel t={t} state={translated} droppedFrames={0} view="translation" />);
    expect(screen.queryByText("Qual é o prazo?")).toBeNull();
    expect(screen.getByText("What is the deadline?")).toBeTruthy();
    rerender(<TranscriptPanel t={t} state={translated} droppedFrames={0} view="original" />);
    expect(screen.getByText("Qual é o prazo?")).toBeTruthy();
    expect(screen.queryByTestId("translation")).toBeNull();
  });

  it("offers the view selector only when there are translations", () => {
    let chosen = "";
    const { rerender } = render(<TranscriptPanel t={t} state={state} droppedFrames={0} onViewChange={(v) => (chosen = v)} />);
    expect(screen.queryByLabelText("Mostrar")).toBeNull();
    rerender(<TranscriptPanel t={t} state={translated} droppedFrames={0} onViewChange={(v) => (chosen = v)} />);
    fireEvent.change(screen.getByLabelText("Mostrar"), { target: { value: "translation" } });
    expect(chosen).toBe("translation");
  });
});

describe("linkLabel", () => {
  it("describes each link state", () => {
    expect(linkLabel(t, null)).toBeNull();
    expect(linkLabel(t, { state: "reconnecting", attempt: 3, retry_in_ms: 500 })?.text).toContain("#3");
    const failed = linkLabel(t, { state: "failed", reason: "cota esgotada", code: 4402 });
    expect(failed?.tone).toBe("bad");
    expect(failed?.text).toContain("cota esgotada");
  });
});
