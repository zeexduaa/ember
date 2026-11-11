# The most common words in a text, using the standard library.
import "text"

let passage = "the quick brown fox jumps over the lazy dog the fox barks and the dog sleeps"
let counts = text.word_counts(passage)
let ranked = counts.keys().sort_by(fn(w) { -counts[w] * 1000 + w.len() })
for w in ranked.slice(0, 4) {
    print(text.pad_right(w, 6, " ") + "#".repeat(counts[w]))
}
print("distinct words:", counts.len(), "| palindrome?", text.is_palindrome("A man a plan a canal Panama"))
