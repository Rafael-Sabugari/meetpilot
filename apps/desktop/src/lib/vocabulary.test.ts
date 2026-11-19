import { describe, expect, it } from "vitest";
import { vocabularyFrom } from "./vocabulary";

describe("vocabularyFrom", () => {
  it("keeps names and jargon, most frequent first, and skips sentence words", () => {
    const ctx =
      "Sou dev Salesforce. Na CI&T trabalho com Apex, LWC e SOQL. Usei Trigger.new e Omni-Channel. " +
      "Projeto Itaú com Flow e Salesforce. Resultado: tudo certo. Também JavaScript e Data Cloud.";
    const v = vocabularyFrom(ctx)!;
    expect(v.startsWith("Salesforce,")).toBe(true);
    for (const t of ["Apex", "LWC", "SOQL", "Trigger.new", "Omni-Channel", "Itaú", "Flow", "JavaScript", "Data", "Cloud"]) {
      expect(v).toContain(t);
    }
    for (const t of ["Sou", "Na", "Projeto Itaú", "Resultado", "Também"]) {
      expect(v.split(", ")).not.toContain(t);
    }
  });

  it("is bounded and empty without context", () => {
    expect(vocabularyFrom("")).toBeNull();
    expect(vocabularyFrom(null)).toBeNull();
    const many = Array.from({ length: 300 }, (_, i) => `Termo${i}X`).join(" ");
    expect(vocabularyFrom(many)!.length).toBeLessThanOrEqual(601);
  });
});
