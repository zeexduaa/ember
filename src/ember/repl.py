"""The interactive prompt.

Each input is checked and compiled like a tiny program, but against the same
checker scope and VM globals as the inputs before it, so definitions carry
over. An input with errors changes nothing. The value of a final expression is
printed (strings in quotes, so "1" and 1 look different).

Input continues on the next line while a block or bracket is still open.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import IO

from . import __version__
from .checker import Checker
from .compiler import compile_program
from .lexer import Lexer
from .modules import ModuleLoader
from .parser import Parser
from .source import EmberError, Source
from .types import resolve
from .values import format_value
from .vm import VM, RuntimeFailure

HELP = """\
Type ember code; definitions stay until you leave.
  :type EXPR   show the type of an expression without running it
  :reset       forget everything defined so far
  :help        this message
  :quit        leave (or press Ctrl+D)"""


class Session:
    def __init__(self, out: IO[str] | None = None, stdin: IO[str] | None = None):
        self.out = out or sys.stdout
        self.stdin = stdin or sys.stdin
        self.reset()

    def reset(self) -> None:
        self.loader = ModuleLoader()
        self.checker = Checker(Source("", "<repl>"), "main", self.loader.importer(Path.cwd() / "<repl>"), repl=True)
        self.vm = VM(out=self.out, stdin=self.stdin)
        self.globals: dict = {}
        self.count = 0

    def needs_more(self, text: str) -> bool:
        """True if the input stops in the middle of something (an open block, call or list)."""
        try:
            Parser(Lexer(Source(text, "<repl>")).tokenize(), Source(text, "<repl>")).parse_program()
        except EmberError as exc:
            return any("end of file" in d.message for d in exc.diagnostics)
        return False

    def type_of(self, text: str) -> str:
        source = Source(text, "<repl>")
        tokens = Lexer(source).tokenize()
        parser = Parser(tokens, source)
        expr = parser.expression()
        snapshot = dict(self.checker.globals.names)
        self.checker.errors, self.checker.source = [], source
        try:
            t = self.checker.check_expr(expr)
        except Exception:
            t = None
        finally:
            self.checker.globals.names = snapshot
        if self.checker.errors:
            raise EmberError(self.checker.errors, source)
        return str(resolve(t)) if t is not None else "?"

    def eval(self, text: str) -> str | None:
        """Runs one input. Returns the text to show for its value, if it has one."""
        self.count += 1
        source = Source(text, f"<repl:{self.count}>")
        program = Parser(Lexer(source).tokenize(), source).parse_program()
        checker = self.checker
        snapshot = dict(checker.globals.names)
        checker.errors, checker.warnings, checker.pending_vars, checker.source = [], [], [], source
        checker.check_program(program)
        if checker.errors:
            checker.globals.names = snapshot      # a failed input defines nothing
            raise EmberError(sorted(checker.errors, key=lambda d: d.span.start if d.span else -1), source,
                             related=checker.imported_errors)
        proto = compile_program(program, source, return_last=True)
        self.vm.module_protos.update({m.id: m.proto for m in self.loader.modules.values()})
        result = self.vm.run_main(proto, self.globals)
        if getattr(proto, "returns_value", False):
            return format_value(result, quoted=True)
        return None

    def loop(self, prompt: str = ">>> ", more: str = "... ") -> int:
        write = self.out.write
        write(f"ember {__version__} - :help for help, Ctrl+D to leave\n")
        while True:
            self.out.write(prompt)
            self.out.flush()
            line = self.stdin.readline()
            if line == "":
                write("\n")
                return 0
            text = line.rstrip("\n")
            stripped = text.strip()
            if not stripped:
                continue
            if stripped.startswith(":"):
                command, _, rest = stripped.partition(" ")
                if command in (":q", ":quit", ":exit"):
                    return 0
                if command == ":help":
                    write(HELP + "\n")
                elif command == ":reset":
                    self.reset()
                    write("(everything forgotten)\n")
                elif command == ":type":
                    try:
                        write(f"{rest.strip()} : {self.type_of(rest)}\n")
                    except EmberError as exc:
                        write(exc.render() + "\n")
                else:
                    write(f"unknown command {command}; try :help\n")
                continue
            while self.needs_more(text):
                self.out.write(more)
                self.out.flush()
                nxt = self.stdin.readline()
                if nxt == "":
                    break
                text += "\n" + nxt.rstrip("\n")
            try:
                shown = self.eval(text)
                for w in self.checker.warnings:
                    if w.message != "this value isn't used":
                        write(w.render(Source(text, f"<repl:{self.count}>")) + "\n")
                if shown is not None:
                    write(shown + "\n")
            except EmberError as exc:
                write(exc.render() + "\n")
            except RuntimeFailure as exc:
                write(exc.render() + "\n")
