"""Turns source text into tokens.

Newlines end statements, as in Go: a ``newline`` token is produced at the end of a
line when its last token could end a statement (a name, a literal, ``)``, ``]``,
``}``, ``return``, ...), unless the line is inside ``( )`` or ``[ ]``, or the next
line starts with ``.`` (a method chain). So

    let total = xs
        .map(fn(x: Int) -> Int { x * 2 })
        .len()

is one statement, and so is a call split across lines inside its parentheses.

Strings can embed expressions: ``"Hello, ${name}!"`` becomes one ``template``
token whose value is a list of parts, each either text or the tokens of an
expression.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .source import Diagnostic, EmberError, Source, Span

KEYWORDS = {
    "fn", "let", "var", "if", "else", "while", "for", "in", "return", "break", "continue",
    "struct", "enum", "impl", "match", "true", "false", "nil", "and", "or", "not",
    "import", "as", "pub", "self",
}

# Longest first, so "..=" wins over ".." and "..".
OPERATORS = sorted([
    "==", "!=", "<=", ">=", "->", "=>", "..=", "..", "??", "+=", "-=", "*=", "/=", "%=",
    "+", "-", "*", "/", "%", "<", ">", "=", "(", ")", "[", "]", "{", "}", ",", ":", ".", "?", ";",
], key=len, reverse=True)

# A line ending with one of these may end a statement.
ENDS_STATEMENT = {"ident", "int", "float", "str", "template", "true", "false", "nil", "self",
                  "return", "break", "continue", ")", "]", "}", "?"}

ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", "\\": "\\", '"': '"', "$": "$", "'": "'"}


@dataclass
class Token:
    kind: str          # "ident", "int", "float", "str", "template", "newline", "eof", or the keyword/operator itself
    text: str          # the source text of the token
    value: Any         # int/float/str for literals, list of parts for templates, else None
    span: Span

    def __repr__(self) -> str:
        return f"Token({self.kind!r}, {self.text!r})"


class Lexer:
    def __init__(self, source: Source):
        self.source = source
        self.text = source.text
        self.errors: list[Diagnostic] = []

    def tokenize(self) -> list[Token]:
        tokens, _ = self._tokens(0, inside_template=False)
        if self.errors:
            raise EmberError(self.errors, self.source)
        return tokens

    # -- the main loop -------------------------------------------------------

    def _tokens(self, pos: int, inside_template: bool) -> tuple[list[Token], int]:
        """Lexes from ``pos``. Inside a template, stops at the ``}`` that closes ``${``."""
        text = self.text
        tokens: list[Token] = []
        depth = 0          # ( and [ nesting: newlines inside don't end statements
        braces = 0         # { nesting, to find the end of ${...}
        n = len(text)
        while pos < n:
            ch = text[pos]
            if ch == "\n":
                if depth == 0 and tokens and tokens[-1].kind in ENDS_STATEMENT and not self._next_line_continues(pos):
                    tokens.append(Token("newline", "\n", None, Span(pos, pos + 1)))
                pos += 1
                continue
            if ch in " \t\r":
                pos += 1
                continue
            if ch == "#":
                end = text.find("\n", pos)
                pos = n if end == -1 else end
                continue
            if ch.isdigit():
                token, pos = self._number(pos)
                tokens.append(token)
                continue
            if ch.isalpha() or ch == "_":
                start = pos
                while pos < n and (text[pos].isalnum() or text[pos] == "_"):
                    pos += 1
                word = text[start:pos]
                kind = word if word in KEYWORDS else "ident"
                tokens.append(Token(kind, word, None, Span(start, pos)))
                continue
            if ch == '"':
                token, pos = self._string(pos)
                tokens.append(token)
                continue
            if inside_template and ch == "}" and braces == 0:
                return tokens + [Token("eof", "", None, Span(pos, pos))], pos
            for op in OPERATORS:
                if text.startswith(op, pos):
                    if op in "([":
                        depth += 1
                    elif op in ")]":
                        depth = max(0, depth - 1)
                    elif op == "{":
                        braces += 1
                    elif op == "}":
                        braces -= 1
                    tokens.append(Token(op, op, None, Span(pos, pos + len(op))))
                    pos += len(op)
                    break
            else:
                hint = {"&": "use 'and'", "|": "use 'or'", "!": "use 'not' (or != for 'not equal')",
                        "'": 'strings use double quotes: "like this"'}.get(ch)
                self.errors.append(Diagnostic(f"unexpected character {ch!r}", Span(pos, pos + 1),
                                              notes=[hint] if hint else []))
                pos += 1
        if inside_template:
            return tokens, pos  # the caller reports the missing }
        if tokens and tokens[-1].kind in ENDS_STATEMENT:
            tokens.append(Token("newline", "", None, Span(n, n)))
        tokens.append(Token("eof", "", None, Span(n, n)))
        return tokens, pos

    def _next_line_continues(self, newline: int) -> bool:
        """True if the next non-blank, non-comment line starts with '.' (a method chain), but not '..'."""
        pos = newline + 1
        text = self.text
        while pos < len(text):
            ch = text[pos]
            if ch in " \t\r\n":
                pos += 1
            elif ch == "#":
                end = text.find("\n", pos)
                if end == -1:
                    return False
                pos = end + 1
            else:
                return ch == "." and not text.startswith("..", pos)
        return False

    # -- literals ------------------------------------------------------------

    def _number(self, start: int) -> tuple[Token, int]:
        text, pos, n = self.text, start, len(self.text)
        if text.startswith(("0x", "0X", "0b", "0B", "0o", "0O"), pos):
            base = {"x": 16, "b": 2, "o": 8}[text[pos + 1].lower()]
            pos += 2
            digits_start = pos
            while pos < n and (text[pos].isalnum() or text[pos] == "_"):
                pos += 1
            digits = text[digits_start:pos].replace("_", "")
            try:
                value = int(digits, base)
            except ValueError:
                self.errors.append(Diagnostic(f"invalid number {text[start:pos]!r}", Span(start, pos)))
                value = 0
            return Token("int", text[start:pos], value, Span(start, pos)), pos
        while pos < n and (text[pos].isdigit() or text[pos] == "_"):
            pos += 1
        is_float = False
        if pos + 1 < n and text[pos] == "." and text[pos + 1].isdigit():
            is_float = True
            pos += 1
            while pos < n and (text[pos].isdigit() or text[pos] == "_"):
                pos += 1
        if pos < n and text[pos] in "eE":
            look = pos + 1
            if look < n and text[look] in "+-":
                look += 1
            if look < n and text[look].isdigit():
                is_float = True
                pos = look
                while pos < n and text[pos].isdigit():
                    pos += 1
        raw = text[start:pos]
        if raw.endswith("_") or "__" in raw:
            self.errors.append(Diagnostic(f"invalid number {raw!r}: '_' goes between digits", Span(start, pos)))
        clean = raw.replace("_", "")
        if pos < n and (text[pos].isalpha() or text[pos] == "_"):
            end = pos
            while end < n and (text[end].isalnum() or text[end] == "_"):
                end += 1
            self.errors.append(Diagnostic(f"invalid number {text[start:end]!r}", Span(start, end)))
            pos = end
        if is_float:
            return Token("float", raw, float(clean), Span(start, pos)), pos
        return Token("int", raw, int(clean or "0"), Span(start, pos)), pos

    def _string(self, start: int) -> tuple[Token, int]:
        """A string literal, possibly with ${...} parts. ``start`` is the opening quote."""
        text, pos, n = self.text, start + 1, len(self.text)
        parts: list[str | list[Token]] = []
        buf: list[str] = []
        while True:
            if pos >= n or text[pos] == "\n":
                self.errors.append(Diagnostic("string is never closed", Span(start, pos),
                                              notes=["strings end on the same line; use \\n for a line break"]))
                break
            ch = text[pos]
            if ch == '"':
                pos += 1
                break
            if ch == "\\":
                pos, piece = self._escape(pos)
                buf.append(piece)
                continue
            if ch == "$" and text.startswith("${", pos):
                if buf:
                    parts.append("".join(buf))
                    buf = []
                expr_start = pos + 2
                tokens, end = self._tokens(expr_start, inside_template=True)
                if end >= n or text[end] != "}":
                    self.errors.append(Diagnostic("'${' in a string is never closed with '}'", Span(pos, min(end, n))))
                    pos = end
                    break
                if len(tokens) <= 1:
                    self.errors.append(Diagnostic("empty ${} in a string", Span(pos, end + 1)))
                parts.append(tokens if tokens[-1].kind == "eof" else tokens + [Token("eof", "", None, Span(end, end))])
                pos = end + 1
                continue
            buf.append(ch)
            pos += 1
        if buf or not parts:
            parts.append("".join(buf))
        span = Span(start, pos)
        if len(parts) == 1 and isinstance(parts[0], str):
            return Token("str", text[start:pos], parts[0], span), pos
        return Token("template", text[start:pos], parts, span), pos

    def _escape(self, pos: int) -> tuple[int, str]:
        text = self.text
        nxt = text[pos + 1] if pos + 1 < len(text) else ""
        if nxt in ESCAPES:
            return pos + 2, ESCAPES[nxt]
        if nxt == "u" and text.startswith("{", pos + 2):
            end = text.find("}", pos + 3)
            digits = text[pos + 3:end] if end != -1 else ""
            try:
                return end + 1, chr(int(digits, 16))
            except (ValueError, OverflowError):
                self.errors.append(Diagnostic("invalid \\u{...} escape", Span(pos, (end + 1) if end != -1 else pos + 2),
                                              notes=["write the code point in hex, e.g. \\u{1F600}"]))
                return (end + 1) if end != -1 else pos + 2, ""
        self.errors.append(Diagnostic(f"unknown escape \\{nxt}", Span(pos, pos + 2),
                                      notes=["known escapes: \\n \\t \\r \\0 \\\\ \\\" \\$ \\u{...}"]))
        return pos + 2, nxt


def tokenize(text: str, name: str = "<input>") -> list[Token]:
    return Lexer(Source(text, name)).tokenize()
