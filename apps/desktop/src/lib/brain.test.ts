import { describe, expect, it } from "vitest";
import { addFact, encodeQuery, inContext, noteFromFile } from "./brain";

describe("second brain helpers", () => {
  it("encodes queries with only safe characters", () => {
    expect(encodeQuery("acme (bulk) 'api'!")).toBe("acme%20%28bulk%29%20%27api%27%21");
    expect(encodeQuery("migração")).toMatch(/^[A-Za-z0-9%]+$/);
  });

  it("adds a fact to My context once", () => {
    expect(addFact(null, " Migrei 2M registros ")).toBe("- Migrei 2M registros");
    const ctx = addFact("Sou o Rafael.", "Migrei 2M registros");
    expect(ctx).toBe("Sou o Rafael.\n- Migrei 2M registros");
    expect(addFact(ctx, "migrei 2m   REGISTROS")).toBe(ctx);
    expect(inContext("Reunião com a Acme", "reuniao com a acme")).toBe(true);
    expect(inContext(null, "x")).toBe(false);
    expect(addFact("x".repeat(60000), "novo")).toBe("x".repeat(60000));
  });

  it("turns an imported file into a note", () => {
    expect(noteFromFile("historias_de_projeto.md", "texto")).toEqual({ title: "historias de projeto", text: "texto" });
    expect(noteFromFile(".md", "a".repeat(70000)).text).toHaveLength(60000);
  });
});
