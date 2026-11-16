//! Post-processing of Whisper output.

/// Phrases Whisper is known to "hear" in silence or music (common in PT and EN subtitles
/// datasets). Dropped only when they are the *entire* output of an utterance.
const HALLUCINATIONS: &[&str] = &[
    "obrigado por assistir",
    "obrigada por assistir",
    "obrigado",
    "obrigada",
    "legendas pela comunidade amaraorg",
    "inscrevase no canal",
    "tchau",
    "thank you for watching",
    "thanks for watching",
    "thank you",
    "subtitles by the amaraorg community",
    "you",
    "bye",
];

fn normalize(s: &str) -> String {
    s.to_lowercase()
        .chars()
        .filter(|c| c.is_alphanumeric() || c.is_whitespace())
        .collect::<String>()
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
}

/// Clean one utterance's text. Returns `None` if nothing real remains.
pub fn clean(raw: &str) -> Option<String> {
    let text = raw.split_whitespace().collect::<Vec<_>>().join(" ");
    // Non-speech annotations like "[Música]", "(risos)", "♪".
    let stripped: String = {
        let mut out = String::new();
        let mut depth = 0i32;
        for c in text.chars() {
            match c {
                '[' | '(' => depth += 1,
                ']' | ')' => depth = (depth - 1).max(0),
                '♪' | '♫' => {}
                _ if depth == 0 => out.push(c),
                _ => {}
            }
        }
        out.split_whitespace().collect::<Vec<_>>().join(" ")
    };
    let norm = normalize(&stripped);
    if norm.is_empty() || HALLUCINATIONS.contains(&norm.as_str()) {
        return None;
    }
    Some(stripped)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn keeps_real_speech() {
        assert_eq!(
            clean("  Qual é o prazo   do projeto? ").as_deref(),
            Some("Qual é o prazo do projeto?")
        );
        assert_eq!(
            clean("Obrigado pela reunião de hoje.").as_deref(),
            Some("Obrigado pela reunião de hoje.")
        );
    }

    #[test]
    fn drops_known_hallucinations_and_annotations() {
        for h in [
            "Obrigado por assistir!",
            " obrigado. ",
            "Legendas pela comunidade Amara.org",
            "[Música]",
            "(risos)",
            "♪ ♪",
            "Thank you.",
            "",
            "   ",
        ] {
            assert_eq!(clean(h), None, "{h:?}");
        }
        assert_eq!(
            clean("[Música] Bom dia a todos").as_deref(),
            Some("Bom dia a todos")
        );
    }
}
