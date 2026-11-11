"""Runs the whole pipeline: source -> tokens -> tree -> checked tree -> bytecode -> result."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import IO, Any

from .bytecode import disassemble
from .checker import check
from .compiler import compile_program
from .lexer import Lexer
from .parser import Parser
from .source import Diagnostic, EmberError, Source
from .values import FunctionProto
from .vm import VM, RuntimeFailure


@dataclass
class Compiled:
    source: Source
    proto: FunctionProto
    warnings: list[Diagnostic] = field(default_factory=list)


def compile_source(text: str, name: str = "<input>") -> Compiled:
    """Lexes, parses, checks and compiles. Raises EmberError with every problem found."""
    source = Source(text, name)
    tokens = Lexer(source).tokenize()
    program = Parser(tokens, source).parse_program()
    checker = check(program, source)
    proto = compile_program(program, source)
    return Compiled(source, proto, checker.warnings)


def run_source(text: str, name: str = "<input>", out: IO[str] | None = None, stdin: IO[str] | None = None) -> Any:
    """Compiles and runs. Raises EmberError (compile errors) or RuntimeFailure (runtime errors)."""
    compiled = compile_source(text, name)
    vm = VM(out=out, stdin=stdin)
    return vm.run_main(compiled.proto)


def dis_source(text: str, name: str = "<input>") -> str:
    return disassemble(compile_source(text, name).proto)


__all__ = ["compile_source", "run_source", "dis_source", "EmberError", "RuntimeFailure"]
_ = sys
