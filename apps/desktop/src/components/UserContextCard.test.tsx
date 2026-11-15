import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { UserContextCard } from "./UserContextCard";
import { GroundingBadge } from "./CopilotPanel";
import { makeT } from "../lib/i18n";

const t = makeT("pt");
afterEach(cleanup);

describe("UserContextCard", () => {
  it("saves on blur, trimmed, once per change", () => {
    const saved: (string | null)[] = [];
    render(<UserContextCard t={t} value={null} onSave={(v) => saved.push(v)} />);
    const box = screen.getByLabelText("Meu contexto");
    fireEvent.change(box, { target: { value: "  Sou o Rafael, dev backend.  " } });
    expect(saved).toEqual([]);
    fireEvent.blur(box);
    fireEvent.blur(box);
    expect(saved).toEqual(["Sou o Rafael, dev backend."]);
  });

  it("clearing the text removes the context", () => {
    const saved: (string | null)[] = [];
    render(<UserContextCard t={t} value="algo" onSave={(v) => saved.push(v)} />);
    const box = screen.getByLabelText("Meu contexto");
    fireEvent.change(box, { target: { value: "   " } });
    fireEvent.click(screen.getByText("Salvar"));
    expect(saved).toEqual([null]);
  });

  it("explains where the text is kept", () => {
    render(<UserContextCard t={t} value={null} onSave={() => {}} />);
    expect(screen.getByText(/Salvo só neste computador/)).toBeTruthy();
  });

  it("labels answers based on the user's context", () => {
    render(<GroundingBadge t={t} grounding="from_user_context" />);
    expect(screen.getByTestId("grounding").textContent).toBe("Do seu contexto");
    expect(screen.getByTestId("grounding").className).toContain("pill--ok");
  });
});
