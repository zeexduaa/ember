# String helpers. import "text"

pub fn pad_left(s: Str, width: Int, fill: Str) -> Str {
    if s.len() >= width or fill.is_empty() { return s }
    fill.repeat(width - s.len()).slice(0, width - s.len()) + s
}

pub fn pad_right(s: Str, width: Int, fill: Str) -> Str {
    if s.len() >= width or fill.is_empty() { return s }
    s + fill.repeat(width - s.len()).slice(0, width - s.len())
}

pub fn capitalize(s: Str) -> Str {
    if s.is_empty() { return s }
    s.slice(0, 1).upper() + s.slice(1, s.len())
}

# Splits on runs of spaces, tabs and newlines, dropping empty pieces.
pub fn words(s: Str) -> List[Str] {
    var out: List[Str] = []
    var current = ""
    for ch in s {
        if ch == " " or ch == "\t" or ch == "\n" {
            if not current.is_empty() {
                out.push(current)
                current = ""
            }
        } else {
            current += ch
        }
    }
    if not current.is_empty() { out.push(current) }
    out
}

pub fn reverse(s: Str) -> Str {
    s.chars().reversed().join("")
}

pub fn is_palindrome(s: Str) -> Bool {
    let cleaned = s.lower().chars().filter(fn(c) { c != " " }).join("")
    cleaned == reverse(cleaned)
}

# How many times each word appears.
pub fn word_counts(s: Str) -> Map[Str, Int] {
    var counts: Map[Str, Int] = {}
    for w in words(s.lower()) {
        counts[w] = counts.get_or(w, 0) + 1
    }
    counts
}
