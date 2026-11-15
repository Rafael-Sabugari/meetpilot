import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { CONSENT_KEY, ConsentDialog, hasTranscriptionConsent } from "./ConsentDialog";
import { makeT } from "../lib/i18n";

const t = makeT("pt");
beforeEach(() => localStorage.clear());
afterEach(cleanup);

describe("ConsentDialog", () => {
  it("explains where audio goes and cannot be accepted without the checkbox", () => {
    const onAccept = vi.fn();
    render(<ConsentDialog t={t} onAccept={onAccept} onCancel={() => undefined} />);
    expect(screen.getByRole("dialog").textContent).toMatch(/Deepgram/);
    expect(screen.getByRole("dialog").textContent).toMatch(/não grava o áudio/);
    const accept = screen.getByText("Ativar transcrição") as HTMLButtonElement;
    expect(accept.disabled).toBe(true);
    fireEvent.click(accept);
    expect(onAccept).not.toHaveBeenCalled();
    expect(hasTranscriptionConsent()).toBe(false);
  });

  it("records consent only when accepted", () => {
    const onAccept = vi.fn();
    render(<ConsentDialog t={t} onAccept={onAccept} onCancel={() => undefined} />);
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByText("Ativar transcrição"));
    expect(onAccept).toHaveBeenCalledOnce();
    expect(localStorage.getItem(CONSENT_KEY)).toBeTruthy();
  });

  it("cancel leaves consent unrecorded", () => {
    const onCancel = vi.fn();
    render(<ConsentDialog t={t} onAccept={() => undefined} onCancel={onCancel} />);
    fireEvent.click(screen.getByText("Cancelar"));
    expect(onCancel).toHaveBeenCalledOnce();
    expect(hasTranscriptionConsent()).toBe(false);
  });
});
