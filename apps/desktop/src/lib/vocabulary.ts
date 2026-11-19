/**
 * Spelling hints for the free (Whisper) engine: the names and technical terms the user is
 * likely to hear, taken from "Meu contexto". Whisper uses them as its initial prompt, so
 * "Salesforce", "SOQL" or "Trigger.new" come out right instead of "sales force" or "sockle".
 */

const MAX_CHARS = 600;
const MAX_TERMS = 80;

// Capitalised words that are just sentence starts or common words, not names or jargon.
const COMMON = new Set(
  (
    "A O As Os E Em No Na Nos Nas De Da Do Das Dos Um Uma Para Com Por Se Que Eu Você Ele Ela " +
    "Meu Minha Sou Tenho Foi Era É Não Sim Quando Como Onde Depois Antes Também Mas Então " +
    "Resultado Contexto Desafio Ação Tecnologias Situação Use Sem Seu Sua Isso Este Esta " +
    "The An And Or Of In On At To For With Is Are Was I You He She We They My Your It This " +
    "That When How Where After Before Also But Then Yes No If Not"
  ).split(" "),
);

const TERM = /[A-Za-zÀ-ÿ][\wÀ-ÿ]*(?:[.\-/+#][\wÀ-ÿ]+)*/g;

function looksLikeTerm(w: string): boolean {
  if (w.length < 2 || COMMON.has(w)) return false;
  if (/^[A-Z0-9]{2,}$/.test(w)) return true; // SOQL, LWC, SAP, CI
  if (/[.\-/+#]/.test(w) && /[A-Za-z]/.test(w)) return true; // Trigger.new, Omni-Channel
  if (/[a-z][A-Z]/.test(w)) return true; // DevOps, JavaScript
  return /^[A-ZÀ-Ý]/.test(w); // Salesforce, Itaú, Holambra
}

export function vocabularyFrom(context: string | null | undefined): string | null {
  if (!context?.trim()) return null;
  const counts = new Map<string, number>();
  const firstSeen = new Map<string, number>();
  let i = 0;
  for (const m of context.matchAll(TERM)) {
    const w = m[0].replace(/[.\-/]+$/, "");
    if (!looksLikeTerm(w)) continue;
    counts.set(w, (counts.get(w) ?? 0) + 1);
    if (!firstSeen.has(w)) firstSeen.set(w, i++);
  }
  const terms = [...counts.keys()]
    .sort((a, b) => counts.get(b)! - counts.get(a)! || firstSeen.get(a)! - firstSeen.get(b)!)
    .slice(0, MAX_TERMS);
  let out = "";
  for (const t of terms) {
    const next = out ? `${out}, ${t}` : t;
    if (next.length > MAX_CHARS) break;
    out = next;
  }
  return out ? `${out}.` : null;
}
