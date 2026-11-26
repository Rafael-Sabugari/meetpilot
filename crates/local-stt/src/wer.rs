//! Word error rate: how many words a transcript got wrong compared with what was really said
//! (substitutions + deletions + insertions, divided by the reference word count).

fn words(text: &str) -> Vec<String> {
    let folded: String = text
        .to_lowercase()
        .chars()
        .map(|c| match c {
            'á' | 'à' | 'â' | 'ã' | 'ä' => 'a',
            'é' | 'è' | 'ê' | 'ë' => 'e',
            'í' | 'ì' | 'î' | 'ï' => 'i',
            'ó' | 'ò' | 'ô' | 'õ' | 'ö' => 'o',
            'ú' | 'ù' | 'û' | 'ü' => 'u',
            'ç' => 'c',
            c if c.is_alphanumeric() => c,
            _ => ' ',
        })
        .collect();
    folded.split_whitespace().map(str::to_owned).collect()
}

/// Word error rate in 0.0..=1.0+ (can exceed 1 when the transcript adds many words).
pub fn wer(reference: &str, hypothesis: &str) -> f64 {
    let r = words(reference);
    let h = words(hypothesis);
    if r.is_empty() {
        return if h.is_empty() { 0.0 } else { 1.0 };
    }
    let mut prev: Vec<usize> = (0..=h.len()).collect();
    for (i, rw) in r.iter().enumerate() {
        let mut cur = vec![i + 1; h.len() + 1];
        for (j, hw) in h.iter().enumerate() {
            let sub = prev[j] + usize::from(rw != hw);
            cur[j + 1] = sub.min(prev[j + 1] + 1).min(cur[j] + 1);
        }
        prev = cur;
    }
    prev[h.len()] as f64 / r.len() as f64
}

#[cfg(test)]
mod tests {
    use super::wer;

    #[test]
    fn counts_word_errors_ignoring_case_accents_and_punctuation() {
        assert_eq!(wer("Olá, mundo!", "ola mundo"), 0.0);
        assert_eq!(wer("a migração foi rápida", "a migração foi"), 0.25);
        assert_eq!(wer("bulk api", "bulk ap i"), 1.0);
        assert!((wer("um dois tres quatro", "um dois três cinco") - 0.25).abs() < 1e-9);
        assert_eq!(wer("", ""), 0.0);
    }
}
