import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { makeT } from "../lib/i18n";
import type { CopilotItem } from "../lib/copilot";

const calls: unknown[] = [];
let fail = false;
vi.mock("../lib/api", () => ({
  isTauri: () => false,
  toCommandError: (e: unknown) => ({ kind: "x", message: String(e) }),
  api: { brain: (_m: string, _p: string, body: unknown) => (calls.push(body), fail ? Promise.reject("x") : Promise.resolve(null)) },
}));

import { RateButtons } from "./CopilotPanel";

const t = makeT("pt");
const item: CopilotItem = {
  id: "q-abc-1",
  mode: "auto",
  question: "Como você lida com limites?",
  status: "done",
  answer: { answer: "Bulkifico tudo.", answer_translation: null, grounding: "general", explanation: "", missing_info: [] } as never,
};
afterEach(() => {
  cleanup();
  calls.length = 0;
  fail = false;
});

describe("RateButtons", () => {
  it("rates, switches and removes the rating", async () => {
    render(<RateButtons t={t} item={item} />);
    fireEvent.click(screen.getByLabelText(/Boa/));
    expect(calls[0]).toMatchObject({ question_id: "q-abc-1", question: "Como você lida com limites?", answer: "Bulkifico tudo.", rating: 1 });
    expect(screen.getByLabelText(/Boa/).getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(screen.getByLabelText(/Ruim/));
    expect(calls[1]).toMatchObject({ rating: -1 });
    fireEvent.click(screen.getByLabelText(/Ruim/));
    expect(calls[2]).toMatchObject({ rating: 0 });
  });

  it("undoes the rating when saving fails", async () => {
    fail = true;
    render(<RateButtons t={t} item={item} />);
    fireEvent.click(screen.getByLabelText(/Boa/));
    expect(await screen.findByText("Não deu para salvar")).toBeTruthy();
    await waitFor(() => expect(screen.getByLabelText(/Boa/).getAttribute("aria-pressed")).toBe("false"));
  });
});
