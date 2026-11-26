import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { makeT } from "../lib/i18n";

const calls: string[] = [];
vi.mock("../lib/api", () => ({
  isTauri: () => false,
  toCommandError: (e: unknown) => ({ kind: "x", message: String(e) }),
  api: {
    benchmarkStart: () => (calls.push("start"), Promise.resolve()),
    benchmarkCancel: () => (calls.push("cancel"), Promise.resolve()),
    benchmarkFinish: (ref: string, lang: string, model: string | null) => {
      calls.push(`finish:${lang}:${model}:${ref.slice(0, 10)}`);
      return Promise.resolve({
        audio_seconds: 19.5,
        results: [
          { engine: "whisper", label: "Whisper large-v3-turbo", wer: 0.12, seconds: 2.1, text: "na reunião de ontem…", error: null },
          { engine: "apple", label: "Apple (macOS)", wer: 0.05, seconds: 0.9, text: "Na reunião de ontem…", error: null },
        ],
      });
    },
  },
}));

import { TranscriptionTest, REFERENCE } from "./TranscriptionTest";

const t = makeT("pt");
afterEach(() => {
  cleanup();
  calls.length = 0;
});

describe("TranscriptionTest", () => {
  it("records, compares engines and marks the most accurate", async () => {
    render(<TranscriptionTest t={t} language="pt-BR" model="large-v3-turbo-q5_0" disabled={false} />);
    expect(screen.getByText(REFERENCE["pt-BR"])).toBeTruthy();
    fireEvent.click(screen.getByText(/Começar e ler/));
    fireEvent.click(await screen.findByText("Terminei, comparar"));
    expect(await screen.findByText("5%")).toBeTruthy();
    expect(screen.getByText("12%")).toBeTruthy();
    expect(screen.getByText(/🏆/).textContent).toContain("Apple");
    expect(calls).toEqual(["start", "finish:pt-BR:large-v3-turbo-q5_0:Na reunião"]);
    expect(screen.getByText(/A gravação foi descartada/)).toBeTruthy();
  });

  it("cannot start during a meeting", () => {
    render(<TranscriptionTest t={t} language="en" model={null} disabled />);
    expect((screen.getByText(/Começar e ler/) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(REFERENCE.en)).toBeTruthy();
  });
});
