import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { AnswerCard, GroundingBadge } from "./CopilotPanel";
import { makeT } from "../lib/i18n";

const t = makeT("pt");
afterEach(cleanup);

describe("copilot UI", () => {
  it("labels where every answer comes from", () => {
    render(<GroundingBadge t={t} grounding="needs_confirmation" />);
    expect(screen.getByTestId("grounding").textContent).toBe("Precisa confirmar");
    expect(screen.getByTestId("grounding").className).toContain("pill--warn");
  });

  it("shows the answer, its basis and what must be confirmed", () => {
    render(
      <AnswerCard
        t={t}
        item={{
          id: "m-1",
          mode: "reply",
          question: null,
          status: "done",
          answer: {
            answer: "Tenho [anos de experiência] com Kubernetes.",
            grounding: "needs_confirmation",
            explanation: "A pergunta é sobre sua experiência, que não aparece na conversa.",
            missing_info: ["anos de experiência com Kubernetes"],
            provider: "openai",
            requests_remaining: 150,
          },
        }}
      />,
    );
    expect(screen.getByText("Como respondo?")).toBeTruthy();
    expect(screen.getByText(/Tenho \[anos de experiência\]/)).toBeTruthy();
    expect(screen.getByText(/Para confirmar: anos de experiência com Kubernetes/)).toBeTruthy();
  });

  it("labels automatic suggestions with the detected question, also in the compact overlay", () => {
    render(
      <AnswerCard
        compact
        t={t}
        item={{
          id: "q-system-12000",
          mode: "auto",
          question: "Qual é a sua experiência com Kubernetes?",
          status: "done",
          answer: {
            answer: "Trabalhei com [tempo] em clusters de produção.",
            grounding: "needs_confirmation",
            explanation: "",
            missing_info: ["tempo de experiência"],
            provider: "openai",
            requests_remaining: 140,
          },
        }}
      />,
    );
    const q = screen.getByTestId("auto-question");
    expect(q.textContent).toContain("Pergunta detectada");
    expect(q.textContent).toContain("Qual é a sua experiência com Kubernetes?");
    expect(q.closest("article")?.className).toContain("answer--auto");
    expect(screen.getByTestId("grounding").textContent).toBe("Precisa confirmar");
  });

  it("shows pending and failed states", () => {
    const { rerender } = render(<AnswerCard t={t} item={{ id: "m-2", mode: "ask", question: "Qual o prazo?", status: "pending" }} />);
    expect(screen.getByText("Pensando…")).toBeTruthy();
    rerender(<AnswerCard t={t} item={{ id: "m-2", mode: "ask", question: "Qual o prazo?", status: "failed", error: "a IA demorou demais" }} />);
    expect(screen.getByText("a IA demorou demais")).toBeTruthy();
  });
});
