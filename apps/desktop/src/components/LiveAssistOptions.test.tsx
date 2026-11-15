import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { LiveAssistOptions } from "./LiveAssistOptions";
import { DEFAULT_LIVE, type LivePrefs } from "../lib/liveOptions";
import { makeT } from "../lib/i18n";

const t = makeT("pt");
afterEach(cleanup);

describe("LiveAssistOptions", () => {
  it("changes automatic suggestions and translation target", () => {
    let last: LivePrefs = DEFAULT_LIVE;
    render(<LiveAssistOptions t={t} prefs={DEFAULT_LIVE} onChange={(p) => (last = p)} disabled={false} />);
    const auto = screen.getByLabelText("Sugestões automáticas") as HTMLSelectElement;
    expect(auto.value).toBe("directed");
    expect(screen.getByText("Só perguntas para mim")).toBeTruthy();
    fireEvent.change(auto, { target: { value: "off" } });
    expect(last.autoSuggestions).toBe("off");
    fireEvent.change(screen.getByLabelText("Traduzir transcrição para"), { target: { value: "en" } });
    expect(last.translateTo).toBe("en");
    fireEvent.change(screen.getByLabelText("Traduzir transcrição para"), { target: { value: "none" } });
    expect(last.translateTo).toBeNull();
  });

  it("explains that automatic suggestions use copilot questions, and hides it when off", () => {
    const { rerender } = render(<LiveAssistOptions t={t} prefs={DEFAULT_LIVE} onChange={() => {}} disabled={false} />);
    expect(screen.getByText(/conta como uma pergunta ao copiloto/)).toBeTruthy();
    rerender(<LiveAssistOptions t={t} prefs={{ ...DEFAULT_LIVE, autoSuggestions: "off" }} onChange={() => {}} disabled />);
    expect(screen.queryByText(/conta como uma pergunta ao copiloto/)).toBeNull();
    expect((screen.getByLabelText("Sugestões automáticas") as HTMLSelectElement).disabled).toBe(true);
  });
});
