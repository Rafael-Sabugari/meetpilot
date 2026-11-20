import { describe, expect, it } from "vitest";
import { pendingFromOthers } from "./pending";
import { emptyTranscript, type TranscriptState } from "./transcript";

const seg = (id: string, text: string, source: "system" | "microphone" = "system") => ({
  segment_id: id,
  source,
  text,
  language: "en",
  start_ms: 0,
  end_ms: 1,
  is_final: true,
  speaker: null,
});

describe("pendingFromOthers", () => {
  it("collects what Others said after the last send, plus the line being spoken", () => {
    const state: TranscriptState = {
      ...emptyTranscript,
      finals: [seg("s-1", "Hi."), seg("m-1", "Hello!", "microphone"), seg("s-2", "Tell me about"), seg("s-3", "your last project.")],
      partials: { system: { ...seg("s-4", "And the team size?"), is_final: false } },
    };
    const all = pendingFromOthers(state, null);
    expect(all.text).toBe("Hi. Tell me about your last project. And the team size?");
    expect(all.count).toBe(4);
    expect(all.lastId).toBe("s-3");

    const after = pendingFromOthers(state, "s-1");
    expect(after.text).toBe("Tell me about your last project. And the team size?");
    const none = pendingFromOthers({ ...state, partials: {} }, "s-3");
    expect(none).toEqual({ text: "", count: 0, lastId: "s-3" });
  });
});
