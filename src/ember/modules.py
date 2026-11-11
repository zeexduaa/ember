"""Finding, checking and compiling imported files.

``import "geometry"`` in ``app/main.em`` loads ``app/geometry.em``; a path with
slashes (``import "lib/shapes"``) is relative to the importing file too. If no
such file exists, ember's own standard library is tried (``import "math"``).

Each file is checked and compiled once, however many files import it, and runs
the first time it's imported. Only declarations marked ``pub`` are visible to
importers. Importing a file that (directly or not) imports the first one is an
error that shows the cycle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import ast as A
from .checker import Checker
from .compiler import compile_program
from .lexer import Lexer
from .parser import Parser
from .source import Diagnostic, EmberError, Source
from .types import ModuleT
from .values import FunctionProto

STDLIB = Path(__file__).parent / "stdlib"


@dataclass
class LoadedModule:
    id: str
    name: str
    path: Path
    type: ModuleT
    proto: FunctionProto
    warnings: list[Diagnostic] = field(default_factory=list)


class ModuleLoader:
    def __init__(self) -> None:
        self.modules: dict[str, LoadedModule] = {}
        self.loading: list[str] = []          # the chain of files being loaded, for cycle errors

    def resolve(self, path: str, from_dir: Path) -> Path | None:
        relative = path if path.endswith(".em") else path + ".em"
        candidate = (from_dir / relative).resolve()
        if candidate.is_file():
            return candidate
        library = (STDLIB / relative).resolve()
        if library.is_file():
            return library
        return None

    def importer(self, from_file: Path | None):
        """The callback the checker uses for each import in ``from_file``."""
        from_dir = from_file.parent if from_file else Path.cwd()

        def import_module(stmt: A.Import) -> ModuleT:
            found = self.resolve(stmt.path, from_dir)
            if found is None:
                wanted = display_name((from_dir / (stmt.path if stmt.path.endswith(".em") else stmt.path + ".em")))
                raise EmberError([Diagnostic(
                    f"can't find a module \"{stmt.path}\"", stmt.path_span,
                    notes=[f"looked for {wanted} and in the standard library"])])
            module = self.load(found)
            stmt.module_id = module.id
            return module.type

        return import_module

    def load(self, path: Path) -> LoadedModule:
        module_id = str(path)
        if module_id in self.modules:
            return self.modules[module_id]
        if module_id in self.loading:
            chain = self.loading[self.loading.index(module_id):] + [module_id]
            names = " -> ".join(Path(p).name for p in chain)
            raise EmberError([Diagnostic(f"import cycle: {names}", None,
                                         notes=["move what both files need into a third file"])])
        self.loading.append(module_id)
        try:
            text = path.read_text(encoding="utf-8")
            shown = display_name(path)
            source = Source(text, shown)
            program = Parser(Lexer(source).tokenize(), source).parse_program()
            checker = Checker(source, module_id, self.importer(path))
            checker.check_program(program)
            if checker.errors:
                raise EmberError(sorted(checker.errors, key=lambda d: d.span.start if d.span else -1), source,
                                 related=checker.imported_errors)
            module_type = ModuleT(path.stem, module_id)
            module_type.members = checker.public_members()
            proto = compile_program(program, source, module_id)
            loaded = LoadedModule(module_id, path.stem, path, module_type, proto, checker.warnings)
            self.modules[module_id] = loaded
            return loaded
        finally:
            self.loading.pop()


def display_name(path: Path) -> str:
    path = path.resolve()
    try:
        return str(path.relative_to(Path.cwd().resolve()))
    except ValueError:
        if STDLIB in path.parents:
            return f"<stdlib>/{path.name}"
        return str(path)
