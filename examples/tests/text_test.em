import "text"

fn test_padding() {
    assert_eq(text.pad_left("7", 3, "0"), "007")
    assert_eq(text.pad_right("ab", 4, "."), "ab..")
    assert_eq(text.pad_left("long", 2, "x"), "long", "never cuts")
}

fn test_words_and_counts() {
    assert_eq(text.words("  a  b\tc\n"), ["a", "b", "c"])
    assert_eq(text.word_counts("a b a"), {"a": 2, "b": 1})
}

fn test_palindromes() {
    assert(text.is_palindrome("Never odd or even"), "spaces and case are ignored")
    assert_eq(text.reverse("ember"), "rebme")
}
