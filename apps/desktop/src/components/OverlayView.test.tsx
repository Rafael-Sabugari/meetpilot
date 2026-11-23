import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { OverlayAnswers, OverlayTone, PendingSpeech } from "./OverlayView";
import type { CopilotItem } from "../lib/copilot";
import { makeT } from "../lib/i18n";

const t = makeT("pt");
const done = (id: string, question: string, answer: string): CopilotItem => ({
  id,
  mode: "auto",
  question,
  status: "done",
  answer: { answer, grounding: "from_conversation", explanation: "", missing_info: [], provider: "anthropic", requests_remaining: 10 },
});

describe("OverlayAnswers", () => {
  it("keeps every answer, newest first, when a new question arrives", () => {
    const items: CopilotItem[] = [
      { id: "q-3", mode: "auto", question: "E o prazo?", status: "pending" },
      done("q-2", "Qual sua experiência com Apex?", "Trabalho com Apex há quatro anos."),
      done("q-1", "Pode se apresentar?", "Sou desenvolvedor Salesforce."),
    ];
    render(<OverlayAnswers t={t} items={items} />);
    const box = screen.getByTestId("overlay-answers");
    expect(box.hasAttribute("data-no-drag")).toBe(true);
    const questions = screen.getAllByTestId("auto-question").map((q) => q.textContent);
    expect(questions).toEqual([
      expect.stringContaining("E o prazo?"),
      expect.stringContaining("Qual sua experiência com Apex?"),
      expect.stringContaining("Pode se apresentar?"),
    ]);
    expect(screen.getByText("Trabalho com Apex há quatro anos.")).toBeTruthy();
    expect(screen.getByText("Sou desenvolvedor Salesforce.")).toBeTruthy();
  });

  it("shows the answer in the user's language under the spoken one", () => {
    const item = done("q-9", "What is your experience with Apex?", "I've worked with Apex for four years.");
    item.answer!.answer_translation = "Trabalho com Apex há quatro anos.";
    render(<OverlayAnswers t={t} items={[item, done("q-8", "Hi?", "Hello.")]} />);
    const trs = screen.getAllByTestId("answer-translation");
    expect(trs).toHaveLength(1);
    expect(trs[0].textContent).toBe("Trabalho com Apex há quatro anos.");
  });

  it("shows what the others said above an answer the user asked for", () => {
    const item: CopilotItem = { ...done("r-1", "Was it after a deployment?", "It wasn't tied to a release."), mode: "reply" };
    render(<OverlayAnswers t={t} items={[item]} />);
    expect(screen.getByTestId("sent-question").textContent).toContain("Was it after a deployment?");
  });

  it("renders nothing without suggestions", () => {
    const { container } = render(<OverlayAnswers t={t} items={[]} />);
    expect(container.innerHTML).toBe("");
  });
});

describe("OverlayTone", () => {
  it("lets the user pick how the next answers should sound", () => {
    const picked: string[] = [];
    render(<OverlayTone t={t} tone="natural" onChange={(x) => picked.push(x)} />);
    const sel = screen.getByLabelText("Tom") as HTMLSelectElement;
    expect([...sel.options].map((o) => o.text)).toEqual(["Natural", "Direto", "Técnico", "Formal"]);
    expect(sel.value).toBe("natural");
    fireEvent.change(sel, { target: { value: "concise" } });
    expect(picked).toEqual(["concise"]);
  });
});

describe("PendingSpeech", () => {
  it("shows what they said so far as one text, with its translation", () => {
    render(<PendingSpeech t={t} text="Tell me about your last project." translation="Me fala do seu último projeto." />);
    const box = screen.getByTestId("pending-speech");
    expect(box.textContent).toContain("Falaram (ainda não enviado)");
    expect(box.textContent).toContain("Tell me about your last project.");
    expect(box.textContent).toContain("Me fala do seu último projeto.");
  });
});
