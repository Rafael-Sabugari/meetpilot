import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { makeT } from "../lib/i18n";
import type { Memory } from "../lib/brain";

const calls: { method: string; path: string; body?: unknown }[] = [];
let memories: Memory[] = [];
vi.mock("../lib/api", () => ({
  toCommandError: (e: unknown) => ({ kind: "x", message: String(e) }),
  isTauri: () => false,
  api: {
    brain: (method: string, path: string, body?: unknown) => {
      calls.push({ method, path, body });
      if (path.startsWith("brain/memories") && method === "GET") return Promise.resolve(memories);
      if (path === "brain/ask")
        return Promise.resolve({
          answer: "Você combinou enviar o portfólio até sexta.",
          sources: [{ id: "m1", title: "Entrevista Acme", kind: "meeting", occurred_at: "2026-11-20T15:00:00Z" }],
          input_tokens: 1,
          output_tokens: 1,
        });
      return Promise.resolve(null);
    },
  },
}));

import { BrainPanel } from "./BrainPanel";

const t = makeT("pt");
const meeting: Memory = {
  id: "m1",
  kind: "meeting",
  status: "ready",
  title: "Entrevista Acme",
  occurred_at: "2026-11-20T15:00:00Z",
  meeting_id: "x",
  summary: "Entrevista técnica com a Acme.",
  details: "## Próximos passos\n- Você: enviar o portfólio até sexta",
  suggestions: ["Migrei 2 milhões de registros com Bulk API."],
};

beforeEach(() => {
  calls.length = 0;
  memories = [meeting];
});
afterEach(cleanup);

function setup(userContext: string | null = "Sou o Rafael.") {
  const changes: (string | null)[] = [];
  render(
    <BrainPanel t={t} locale="pt-BR" userContext={userContext} onUserContextChange={(c) => changes.push(c)} refreshKey={0} />,
  );
  return changes;
}

describe("BrainPanel", () => {
  it("asks the brain with the conversation so far and shows the sources", async () => {
    setup();
    fireEvent.change(screen.getByLabelText("Pergunte ao seu segundo cérebro…"), { target: { value: "O que combinei com a Acme?" } });
    fireEvent.click(screen.getByText("Perguntar", { selector: "form button" }));
    expect(await screen.findByText("Você combinou enviar o portfólio até sexta.")).toBeTruthy();
    const ask = calls.find((c) => c.path === "brain/ask");
    expect(ask?.body).toMatchObject({ question: "O que combinei com a Acme?", history: [], user_context: "Sou o Rafael." });
    // A source opens that memory.
    fireEvent.click(screen.getByText(/Entrevista Acme ·/));
    expect(await screen.findByText("Entrevista técnica com a Acme.")).toBeTruthy();
  });

  it("adds a remembered fact to My context and deletes with a second click", async () => {
    const changes = setup();
    fireEvent.click(screen.getByRole("tab", { name: "Memórias" }));
    fireEvent.click(await screen.findByText("Entrevista Acme"));
    expect(screen.getByText("Novos fatos sobre você")).toBeTruthy();
    fireEvent.click(screen.getByText("Adicionar ao Meu contexto"));
    expect(changes).toEqual(["Sou o Rafael.\n- Migrei 2 milhões de registros com Bulk API."]);
    fireEvent.click(screen.getByText("Apagar"));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    fireEvent.click(screen.getByText("Clique de novo para apagar de vez"));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.path === "brain/memories/m1")).toBe(true));
  });

  it("shows facts already in My context as added", async () => {
    setup("- Migrei 2 milhões de registros com Bulk API.");
    fireEvent.click(screen.getByRole("tab", { name: "Memórias" }));
    fireEvent.click(await screen.findByText("Entrevista Acme"));
    expect((screen.getByText("No Meu contexto") as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByText("Novos fatos sobre você")).toBeNull();
  });

  it("saves a note", async () => {
    setup();
    fireEvent.click(screen.getByRole("tab", { name: "Nova nota" }));
    fireEvent.change(screen.getByLabelText("Título"), { target: { value: "Histórias" } });
    fireEvent.change(screen.getByLabelText(/Texto/), { target: { value: "Na Acme…" } });
    fireEvent.click(screen.getByText("Salvar nota"));
    await waitFor(() =>
      expect(calls.find((c) => c.method === "POST" && c.path === "brain/memories")?.body).toEqual({ title: "Histórias", text: "Na Acme…" }),
    );
  });
});
