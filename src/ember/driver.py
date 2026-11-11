"""Runs the whole pipeline: source -> tokens -> tree -> checked tree -> bytecode -> result."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

from .bytecode import disassemble
from .checker import Checker
from .compiler import compile_program
from .lexer import Lexer
from .modules import ModuleLoader
from .parser import Parser
from .source import Diagnostic, EmberError, Source
from .values import FunctionProto
from .vm import VM, RuntimeFailure


@dataclass
class Compiled:
    source: Source
    proto: FunctionProto
    loader: ModuleLoader
    warnings: list[tuple[Diagnostic, Source]] = field(default_factory=list)

    def render_warnings(self) -> str:
        return "\n\n".join(w.render(src) for w, src in self.warnings)


def compile_source(text: str, name: str = "<input>", path: Path | None = None,
                   loader: ModuleLoader | None = None) -> Compiled:
    """Lexes, parses, checks and compiles a program and everything it imports.
    Raises EmberError with every problem found."""
    loader = loader or ModuleLoader()
    source = Source(text, name)
    program = Parser(Lexer(source).tokenize(), source).parse_program()
    checker = Checker(source, "main", loader.importer(path))
    if path is not None:
        loader.loading.append(str(path))      # so a file that imports this one is reported as a cycle
    try:
        checker.check_program(program)
    finally:
        if path is not None:
            loader.loading.pop()
    if checker.errors:
        errors = sorted(checker.errors, key=lambda d: d.span.start if d.span else -1)
        raise EmberError(errors, source, related=checker.imported_errors)
    proto = compile_program(program, source)
    warnings = [(w, Source(m.path.read_text(encoding="utf-8"), m.proto.source_name))
                for m in loader.modules.values() for w in m.warnings]
    warnings += [(w, source) for w in sorted(checker.warnings, key=lambda d: d.span.start if d.span else -1)]
    return Compiled(source, proto, loader, warnings)


def compile_file(path: str | Path) -> Compiled:
    path = Path(path)
    return compile_source(path.read_text(encoding="utf-8"), str(path), path.resolve())


def make_vm(compiled: Compiled, out: IO[str] | None = None, stdin: IO[str] | None = None) -> VM:
    vm = VM(out=out, stdin=stdin)
    vm.module_protos = {m.id: m.proto for m in compiled.loader.modules.values()}
    return vm


def run_source(text: str, name: str = "<input>", out: IO[str] | None = None, stdin: IO[str] | None = None,
               path: Path | None = None) -> Any:
    """Compiles and runs. Raises EmberError (compile errors) or RuntimeFailure (runtime errors)."""
    compiled = compile_source(text, name, path)
    return make_vm(compiled, out, stdin).run_main(compiled.proto)


def run_file(path: str | Path, out: IO[str] | None = None, stdin: IO[str] | None = None) -> Any:
    compiled = compile_file(path)
    return make_vm(compiled, out, stdin).run_main(compiled.proto)


def dis_source(text: str, name: str = "<input>", path: Path | None = None) -> str:
    compiled = compile_source(text, name, path)
    listing = [disassemble(compiled.proto)]
    for module in compiled.loader.modules.values():
        listing.append(disassemble(module.proto).replace("== <main>", f"== <module {module.name}>", 1))
    return "\n\n".join(listing)


__all__ = ["compile_source", "compile_file", "run_source", "run_file", "dis_source", "EmberError", "RuntimeFailure"]
