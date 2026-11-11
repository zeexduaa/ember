"""Source text, spans and diagnostics shared by every stage.

Every token and AST node carries a :class:`Span` (byte offsets into the source).
Errors are :class:`Diagnostic` objects; a stage that finds problems raises
:class:`EmberError` with all of them, and :func:`render` prints them like:

    error: expected Int, found Str
      --> demo.em:3:14
       |
     3 | let x: Int = "hi"
       |              ^^^^
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Span:
    start: int
    end: int

    def to(self, other: Span) -> Span:
        return Span(min(self.start, other.start), max(self.end, other.end))


class Source:
    def __init__(self, text: str, name: str = "<input>"):
        self.text = text
        self.name = name
        self._line_starts = [0] + [i + 1 for i, ch in enumerate(text) if ch == "\n"]

    def line_col(self, offset: int) -> tuple[int, int]:
        """1-based line and column for an offset."""
        offset = max(0, min(offset, len(self.text)))
        line = bisect.bisect_right(self._line_starts, offset) - 1
        return line + 1, offset - self._line_starts[line] + 1

    def line_text(self, line: int) -> str:
        start = self._line_starts[line - 1]
        end = self.text.find("\n", start)
        return self.text[start:] if end == -1 else self.text[start:end]


@dataclass
class Diagnostic:
    message: str
    span: Span | None
    kind: str = "error"                  # "error" or "warning"
    notes: list[str] = field(default_factory=list)

    def render(self, source: Source | None) -> str:
        head = f"{self.kind}: {self.message}"
        if source is None or self.span is None:
            return "\n".join([head] + [f"  = note: {n}" for n in self.notes])
        line, col = source.line_col(self.span.start)
        end_line, end_col = source.line_col(max(self.span.start, self.span.end - 1))
        text = source.line_text(line)
        width = max(1, (end_col - col + 1) if end_line == line else len(text) - col + 1)
        gutter = " " * len(str(line))
        lines = [
            head,
            f"{gutter}--> {source.name}:{line}:{col}",
            f"{gutter} |",
            f"{line} | {text}",
            f"{gutter} | {' ' * (col - 1)}{'^' * width}",
        ]
        lines += [f"{gutter} = note: {n}" for n in self.notes]
        return "\n".join(lines)


class EmberError(Exception):
    """One or more diagnostics from the lexer, parser or checker."""

    def __init__(self, diagnostics: list[Diagnostic], source: Source | None = None):
        self.diagnostics = diagnostics
        self.source = source
        super().__init__(diagnostics[0].message if diagnostics else "error")

    def render(self) -> str:
        return "\n\n".join(d.render(self.source) for d in self.diagnostics)


def render(diagnostics: list[Diagnostic], source: Source | None) -> str:
    return "\n\n".join(d.render(source) for d in diagnostics)
