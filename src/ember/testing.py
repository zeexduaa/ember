"""`ember test`: runs the test functions in *_test.em files.

A test is a top-level function whose name starts with ``test_`` and that takes
no arguments. Each file's top level runs once, then each test runs on its own:
a failing test (an assert, a panic or any runtime error) doesn't stop the others.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO

from .driver import compile_file, make_vm
from .source import EmberError
from .values import Closure
from .vm import RuntimeFailure


@dataclass
class FileResult:
    path: Path
    passed: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)     # (test name, rendered error)
    error: str | None = None                                          # the file didn't compile or set up


def find_test_files(paths: list[str]) -> list[Path]:
    found: list[Path] = []
    for raw in paths or ["."]:
        path = Path(raw)
        if path.is_dir():
            found.extend(sorted(p for p in path.rglob("*_test.em") if not any(part.startswith(".") for part in p.parts)))
        elif path.is_file():
            found.append(path)
    return found


def run_file(path: Path, pattern: str | None = None) -> FileResult:
    result = FileResult(path)
    try:
        compiled = compile_file(path)
    except EmberError as exc:
        result.error = exc.render()
        return result
    vm = make_vm(compiled)
    globals_: dict = {}
    try:
        vm.run_main(compiled.proto, globals_)      # the file's top level, once
    except RuntimeFailure as exc:
        result.error = exc.render()
        return result
    tests = [(name, value) for name, value in globals_.items()
             if name.startswith("test_") and isinstance(value, Closure)]
    for name, fn in tests:
        if pattern and pattern not in name:
            continue
        if fn.proto.arity != 0:
            result.failed.append((name, "test functions take no arguments"))
            continue
        try:
            vm.call_function(fn, [], globals_)
            result.passed.append(name)
        except RuntimeFailure as exc:
            result.failed.append((name, exc.render()))
    return result


def run_tests(paths: list[str], pattern: str | None = None, out: IO[str] | None = None) -> int:
    out = out or sys.stdout
    files = find_test_files(paths)
    if not files:
        print("no test files found (they're named something_test.em)", file=out)
        return 1
    started = time.perf_counter()
    passed = failed = broken = 0
    for path in files:
        result = run_file(path, pattern)
        print(str(path), file=out)
        if result.error is not None:
            broken += 1
            print("  could not run:", file=out)
            print(_indent(result.error, 4), file=out)
            continue
        for name in result.passed:
            print(f"  ok    {name}", file=out)
        for name, error in result.failed:
            print(f"  FAIL  {name}", file=out)
            print(_indent(error, 8), file=out)
        passed += len(result.passed)
        failed += len(result.failed)
    seconds = time.perf_counter() - started
    summary = f"{passed + failed} test(s): {passed} passed, {failed} failed"
    if broken:
        summary += f", {broken} file(s) couldn't run"
    print(f"\n{summary} ({seconds:.2f} s)", file=out)
    return 1 if failed or broken else 0


def _indent(text: str, n: int) -> str:
    return "\n".join(" " * n + line for line in text.splitlines())
