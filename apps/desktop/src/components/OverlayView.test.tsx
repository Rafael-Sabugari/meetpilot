import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { OverlayAnswers } from "./OverlayView";
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

  it("renders nothing without suggestions", () => {
    const { container } = render(<OverlayAnswers t={t} items={[]} />);
    expect(container.innerHTML).toBe("");
  });
});
