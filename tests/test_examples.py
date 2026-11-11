"""Every example program runs and prints exactly what its .out file says."""

import io
from pathlib import Path

import pytest

from ember.driver import compile_file, make_vm

EXAMPLES = Path(__file__).parent.parent / "examples"
PROGRAMS = sorted(p for p in EXAMPLES.rglob("*.em") if p.with_suffix(".out").exists())


def test_examples_exist():
    assert len(PROGRAMS) >= 8


@pytest.mark.parametrize("path", PROGRAMS, ids=lambda p: str(p.relative_to(EXAMPLES)))
def test_example(path):
    compiled = compile_file(path)
    assert [w.message for w, _ in compiled.warnings] == [], "examples should compile without warnings"
    out = io.StringIO()
    make_vm(compiled, out=out).run_main(compiled.proto)
    assert out.getvalue() == path.with_suffix(".out").read_text(encoding="utf-8")
