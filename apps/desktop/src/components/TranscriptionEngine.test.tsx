import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { TranscriptionEngine, pickModel } from "./TranscriptionEngine";
import type { LocalModels } from "../lib/api";
import { makeT } from "../lib/i18n";

const t = makeT("pt");
afterEach(cleanup);
const MB = 1024 * 1024;
const models = (downloaded: boolean): LocalModels => ({
  available: true,
  models: [
    { id: "base", approx_bytes: 142 * MB, downloaded: false, recommended: false },
    { id: "large-v3-turbo-q5_0", approx_bytes: 547 * MB, downloaded, recommended: true },
  ],
});

function setup(over: Partial<Parameters<typeof TranscriptionEngine>[0]> = {}) {
  const calls: string[] = [];
  render(
    <TranscriptionEngine
      t={t}
      engine="local"
      onChange={(e) => calls.push(`engine:${e}`)}
      models={models(false)}
      progress={null}
      error={null}
      onDownload={(id) => calls.push(`download:${id}`)}
      onCancel={(id) => calls.push(`cancel:${id}`)}
      disabled={false}
      {...over}
    />,
  );
  return calls;
}

describe("TranscriptionEngine", () => {
  it("is hidden where the build has no Whisper engine", () => {
    setup({ models: { available: false, models: [] } });
    expect(screen.queryByTestId("stt-engine")).toBeNull();
  });

  it("offers Deepgram or free Whisper and switches", () => {
    const calls = setup({ engine: "cloud" });
    const sel = screen.getByLabelText("Transcrição") as HTMLSelectElement;
    expect([...sel.options].map((o) => o.text)).toEqual(["Deepgram (nuvem, pago)", "Whisper neste Mac (grátis)"]);
    fireEvent.change(sel, { target: { value: "local" } });
    expect(calls).toEqual(["engine:local"]);
    expect(screen.queryByText(/Baixar modelo/)).toBeNull();
  });

  it("asks to download the recommended model, with its size", () => {
    const calls = setup();
    fireEvent.click(screen.getByText(/Baixar modelo de voz \(~547 MB\)/));
    expect(calls).toEqual(["download:large-v3-turbo-q5_0"]);
    expect(screen.getByText(/o áudio não sai do seu computador/)).toBeTruthy();
  });

  it("shows progress and lets the user cancel", () => {
    const calls = setup({ progress: { id: "large-v3-turbo-q5_0", state: "progress", downloaded: 100 * MB, total: 400 * MB } });
    expect(screen.getByText(/25% · 100 MB/)).toBeTruthy();
    fireEvent.click(screen.getByText("Cancelar"));
    expect(calls).toEqual(["cancel:large-v3-turbo-q5_0"]);
  });

  it("says when the model is ready", () => {
    setup({ models: models(true) });
    expect(screen.getByTestId("model-ready").textContent).toContain("Modelo de voz pronto");
  });

  it("picks the recommended model", () => {
    expect(pickModel(models(false))?.id).toBe("large-v3-turbo-q5_0");
    expect(pickModel(null)).toBeNull();
  });
});
