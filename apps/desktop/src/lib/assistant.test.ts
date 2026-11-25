import { describe, expect, it } from "vitest";
import { FRESH_AFTER_MS, MAX_KEPT, historyFor, loadChat, saveChat, type AssistantMsg } from "./assistant";

const mem = () => {
  const m = new Map<string, string>();
  return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v) };
};

describe("assistant chat", () => {
  it("keeps the last messages and ignores broken storage", () => {
    const s = mem();
    const chat: AssistantMsg[] = Array.from({ length: 50 }, (_, i) => ({ role: i % 2 ? "brain" : "user", text: `m${i}`, at: i }));
    saveChat(chat, s);
    const back = loadChat(s);
    expect(back).toHaveLength(MAX_KEPT);
    expect(back[0].text).toBe("m10");
    s.setItem("meetpilot.assistant.chat", "{oops");
    expect(loadChat(s)).toEqual([]);
    s.setItem("meetpilot.assistant.chat", JSON.stringify([{ role: "x", text: 1 }, { role: "user", text: "ok", at: 1 }]));
    expect(loadChat(s).map((m) => m.text)).toEqual(["ok"]);
  });

  it("starts fresh after a long pause", () => {
    const chat: AssistantMsg[] = [
      { role: "user", text: "oi", at: 1000 },
      { role: "brain", text: "olá", at: 2000 },
    ];
    expect(historyFor(chat, 3000)).toEqual([
      { role: "user", text: "oi" },
      { role: "brain", text: "olá" },
    ]);
    expect(historyFor(chat, 2000 + FRESH_AFTER_MS + 1)).toEqual([]);
    expect(historyFor([], 0)).toEqual([]);
  });
});
