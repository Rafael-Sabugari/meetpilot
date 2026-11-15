import { describe, expect, it } from "vitest";
import { DEFAULT_LIVE, parseLivePrefs, toLiveOptions } from "./liveOptions";

describe("live preferences", () => {
  it("defaults to suggestions for questions asked to the user, no translation", () => {
    expect(parseLivePrefs(null)).toEqual(DEFAULT_LIVE);
    expect(DEFAULT_LIVE.autoSuggestions).toBe("directed");
    expect(DEFAULT_LIVE.translateTo).toBeNull();
  });

  it("keeps valid fields and replaces invalid ones", () => {
    const p = parseLivePrefs(JSON.stringify({ autoSuggestions: "all", tone: "rude", translateTo: "fr", translationView: "translation" }));
    expect(p).toEqual({ autoSuggestions: "all", tone: "natural", translateTo: null, userContext: null, translationView: "translation" });
    expect(parseLivePrefs("{not json")).toEqual(DEFAULT_LIVE);
  });

  it("sends only server options", () => {
    expect(toLiveOptions({ ...DEFAULT_LIVE, translateTo: "en" })).toEqual({ autoSuggestions: "directed", tone: "natural", translateTo: "en", userContext: null });
  });

  it("keeps the user's context, trimmed and bounded, and treats blank as none", () => {
    expect(parseLivePrefs(JSON.stringify({ userContext: "Sou o Rafael" })).userContext).toBe("Sou o Rafael");
    expect(parseLivePrefs(JSON.stringify({ userContext: "   " })).userContext).toBeNull();
    expect(parseLivePrefs(JSON.stringify({ userContext: 42 })).userContext).toBeNull();
    expect(parseLivePrefs(JSON.stringify({ userContext: "x".repeat(5000) })).userContext).toHaveLength(4000);
    expect(toLiveOptions({ ...DEFAULT_LIVE, userContext: "  dev backend  " }).userContext).toBe("dev backend");
    expect(toLiveOptions({ ...DEFAULT_LIVE, userContext: "  " }).userContext).toBeNull();
  });
});
