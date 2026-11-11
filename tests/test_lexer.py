import pytest

from conftest import errors_of
from ember.lexer import tokenize
from ember.source import EmberError, Source, Span, Diagnostic


def kinds(text):
    return [t.kind for t in tokenize(text)]


def test_numbers():
    toks = tokenize("1_000 0xff 0b101 0o17 1.5 2e3 2.5e-2 7")
    assert [(t.kind, t.value) for t in toks[:-2]] == [
        ("int", 1000), ("int", 255), ("int", 5), ("int", 15),
        ("float", 1.5), ("float", 2000.0), ("float", 0.025), ("int", 7)]


def test_range_after_integer_is_not_a_float():
    assert kinds("0..10") == ["int", "..", "int", "newline", "eof"]
    assert kinds("0..=10") == ["int", "..=", "int", "newline", "eof"]


def test_keywords_and_operators():
    assert kinds("fn f(x: Int) -> Int { x ?? 1 }") == [
        "fn", "ident", "(", "ident", ":", "ident", ")", "->", "ident", "{", "ident", "??", "int", "}", "newline", "eof"]
    assert kinds("a += 1; b != c") == ["ident", "+=", "int", ";", "ident", "!=", "ident", "newline", "eof"]


def test_newlines_end_statements_only_when_they_can():
    assert kinds("let x = 1 +\n 2") == ["let", "ident", "=", "int", "+", "int", "newline", "eof"]
    assert kinds("f(a,\n b)\ng()") == ["ident", "(", "ident", ",", "ident", ")", "newline",
                                       "ident", "(", ")", "newline", "eof"]
    assert kinds("xs\n  .len()\n  # comment\n  .abs()") == [
        "ident", ".", "ident", "(", ")", ".", "ident", "(", ")", "newline", "eof"]
    assert kinds("x\n..y") == ["ident", "newline", "..", "ident", "newline", "eof"]  # '..' is not a method chain


def test_strings_and_escapes():
    tok = tokenize(r'"a\tb\n\"q\" \$ \u{1F600}"')[0]
    assert tok.kind == "str" and tok.value == 'a\tb\n"q" $ \U0001F600'


def test_templates_hold_tokens_for_each_expression():
    tok = tokenize('"x=${x + 1}, m=${ {"k": 1}["k"] }"')[0]
    assert tok.kind == "template"
    text, first, middle, second = tok.value
    assert text == "x=" and middle == ", m="
    assert [t.kind for t in first] == ["ident", "+", "int", "eof"]
    assert [t.kind for t in second] == ["{", "str", ":", "int", "}", "[", "str", "]", "eof"]


def test_comments_are_skipped():
    assert kinds("# whole line\nx # trailing\n") == ["ident", "newline", "eof"]


def test_lexer_errors_are_collected():
    assert errors_of(tokenize, 'a & b\n"open') == ["unexpected character '&'", "string is never closed"]
    assert errors_of(tokenize, "12abc") == ["invalid number '12abc'"]
    assert errors_of(tokenize, "1__0") == ["invalid number '1__0': '_' goes between digits"]
    assert errors_of(tokenize, r'"\q"') == ["unknown escape \\q"]
    assert "never closed with '}'" in errors_of(tokenize, '"${x')[0]


def test_diagnostic_rendering_points_at_the_problem():
    source = Source("let a = 1\nlet x: Int = \"hi\"\n", "demo.em")
    start = source.text.index('"hi"')
    text = Diagnostic("expected Int, found Str", Span(start, start + 4), notes=["a note"]).render(source)
    assert text == (
        "error: expected Int, found Str\n"
        " --> demo.em:2:14\n"
        "  |\n"
        '2 | let x: Int = "hi"\n'
        "  |              ^^^^\n"
        "  = note: a note")


def test_hints_for_other_languages_operators():
    with pytest.raises(EmberError) as exc:
        tokenize("a && b")
    assert exc.value.diagnostics[0].notes == ["use 'and'"]


def test_float_literals_that_overflow_are_errors():
    assert errors_of(tokenize, "1e400") == ["1e400 is too big for a Float"]
