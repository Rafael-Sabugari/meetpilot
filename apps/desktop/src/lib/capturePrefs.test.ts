import { describe, expect, it } from "vitest";
import { availableDevice, defaultCapturePrefs, parseCapturePrefs } from "./capturePrefs";

describe("capture preferences", () => {
  it("defaults: computer audio on, mic off, no transcription, language from the system", () => {
    expect(parseCapturePrefs(null, "pt-BR")).toEqual({ ...defaultCapturePrefs("pt-BR"), language: "pt-BR" });
    expect(parseCapturePrefs(null, "en-US").language).toBe("en");
  });

  it("remembers what the user chose and ignores invalid values", () => {
    const p = parseCapturePrefs(
      JSON.stringify({ useMic: true, micDevice: "AirPods Pro de Rafael", transcribe: true, language: "fr", useSystem: "yes" }),
      "pt-BR",
    );
    expect(p).toMatchObject({ useMic: true, micDevice: "AirPods Pro de Rafael", transcribe: true, language: "pt-BR", useSystem: true });
    expect(parseCapturePrefs("{broken", "en")).toEqual(defaultCapturePrefs("en"));
  });

  it("falls back to the default device when the remembered one is not connected", () => {
    expect(availableDevice("AirPods Pro de Rafael", ["MacBook Air Microphone", "AirPods Pro de Rafael"])).toBe("AirPods Pro de Rafael");
    expect(availableDevice("AirPods Pro de Rafael", ["MacBook Air Microphone"])).toBe("");
    expect(availableDevice("", ["x"])).toBe("");
  });
});
