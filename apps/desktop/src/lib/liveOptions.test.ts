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
    expect(p).toEqual({ autoSuggestions: "all", tone: "natural", translateTo: null, translationView: "translation" });
    expect(parseLivePrefs("{not json")).toEqual(DEFAULT_LIVE);
  });

  it("sends only server options", () => {
    expect(toLiveOptions({ ...DEFAULT_LIVE, translateTo: "en" })).toEqual({ autoSuggestions: "directed", tone: "natural", translateTo: "en" });
  });
});
