import { describe, expect, it } from "vitest";
import { detectLocale, makeT, messages } from "./i18n";

describe("i18n", () => {
  it("detects Portuguese variants", () => {
    expect(detectLocale("pt-BR")).toBe("pt");
    expect(detectLocale("pt")).toBe("pt");
    expect(detectLocale("en-US")).toBe("en");
    expect(detectLocale(undefined)).toBe("en");
  });
  it("has a Portuguese string for every English key", () => {
    for (const k of Object.keys(messages.en)) {
      expect(messages.pt[k as keyof typeof messages.en], k).toBeTruthy();
    }
  });
  it("states the no-bot promise in both languages", () => {
    expect(makeT("en")("onboarding.noBot")).toMatch(/never joins/i);
    expect(makeT("pt")("onboarding.noBot")).toMatch(/nunca entra/i);
  });
});
