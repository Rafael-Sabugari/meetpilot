import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { makeT } from "../lib/i18n";

const calls: { path: string; body: unknown }[] = [];
let fail: unknown = null;
vi.mock("../lib/api", () => ({
  isTauri: () => false,
  toCommandError: (e: { kind?: string; message?: string }) => ({ kind: e.kind ?? "x", message: e.message ?? String(e) }),
  api: {
    brain: (_m: string, path: string, body: unknown) => {
      calls.push({ path, body });
      if (fail) return Promise.reject(fail);
      return Promise.resolve({ answer: "Use um handler por objeto.", sources: [], input_tokens: 1, output_tokens: 1 });
    },
  },
}));

import { AssistantView } from "./AssistantView";

const t = makeT("pt");
beforeEach(() => {
  calls.length = 0;
  fail = null;
  localStorage.clear();
  localStorage.setItem("meetpilot.live.v1", JSON.stringify({ userContext: "Sou o Rafael." }));
});
afterEach(cleanup);

const box = () => screen.getByLabelText(/Pergunte qualquer coisa/) as HTMLTextAreaElement;

describe("AssistantView", () => {
  it("sends with Enter in assistant mode, with My context, and keeps the chat", async () => {
    render(<AssistantView t={t} />);
    fireEvent.change(box(), { target: { value: "Como bulkificar uma trigger?" } });
    fireEvent.keyDown(box(), { key: "Enter" });
    expect(await screen.findByText("Use um handler por objeto.")).toBeTruthy();
    expect(calls[0].body).toMatchObject({ question: "Como bulkificar uma trigger?", assistant: true, history: [], user_context: "Sou o Rafael." });
    await waitFor(() => expect(JSON.parse(localStorage.getItem("meetpilot.assistant.chat") ?? "[]")).toHaveLength(2));
    // Shift+Enter does not send.
    fireEvent.change(box(), { target: { value: "linha" } });
    fireEvent.keyDown(box(), { key: "Enter", shiftKey: true });
    expect(calls).toHaveLength(1);
  });

  it("explains when you are not signed in", async () => {
    fail = { kind: "not_signed_in", message: "x" };
    render(<AssistantView t={t} />);
    fireEvent.change(box(), { target: { value: "oi" } });
    fireEvent.keyDown(box(), { key: "Enter" });
    expect(await screen.findByText(/Entre na sua conta/)).toBeTruthy();
  });
});
