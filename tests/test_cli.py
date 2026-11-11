import io

import pytest

from ember.cli import main
from ember.repl import Session


def write(path, text):
    path.write_text(text, encoding="utf-8")
    return str(path)


def run(capsys, *argv):
    code = main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def test_run(tmp_path, capsys):
    path = write(tmp_path / "hi.em", 'let name = "ember"\nprint("hello, ${name}")')
    assert run(capsys, "run", path) == (0, "hello, ember\n", "")


def test_run_reports_compile_errors_and_runtime_errors(tmp_path, capsys):
    bad = write(tmp_path / "bad.em", 'let x: Int = "a"')
    code, out, err = run(capsys, "run", bad)
    assert code == 1 and out == "" and "expected Int, found Str" in err and "bad.em:1:14" in err
    crash = write(tmp_path / "crash.em", 'print("before")\nprint([1][2])')
    code, out, err = run(capsys, "run", crash)
    assert code == 1 and out == "before\n" and "runtime error: index 2 is out of range" in err


def test_warnings_go_to_stderr_unless_quiet(tmp_path, capsys):
    path = write(tmp_path / "w.em", "fn f() { let unused = 1 }\nf()")
    code, out, err = run(capsys, "run", path)
    assert code == 0 and "warning: 'unused' is never used" in err
    assert run(capsys, "run", "-q", path)[2] == ""


def test_check_several_files(tmp_path, capsys):
    good = write(tmp_path / "good.em", "print(1)")
    bad = write(tmp_path / "bad.em", "print(nope)")
    code, out, err = run(capsys, "check", good, bad)
    assert code == 1 and f"{good}: ok" in out and "'nope' isn't defined" in err
    assert run(capsys, "check", good)[:2] == (0, "")


def test_dis_eval_and_missing_files(tmp_path, capsys):
    path = write(tmp_path / "f.em", "fn add(a: Int, b: Int) -> Int { a + b }\nprint(add(1, 2))")
    code, out, _ = run(capsys, "dis", path)
    assert code == 0 and "== add (arity 2, 2 locals) ==" in out and "IADD" in out
    assert run(capsys, "eval", "print(6 * 7)") == (0, "42\n", "")
    code, _, err = run(capsys, "run", str(tmp_path / "missing.em"))
    assert code == 1 and "no such file" in err


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0 and "ember 0.1.0" in capsys.readouterr().out


# -- REPL ------------------------------------------------------------------------------------


def session_output(lines):
    out = io.StringIO()
    Session(out=out, stdin=io.StringIO("\n".join(lines) + "\n")).loop()
    return out.getvalue()


def test_repl_keeps_definitions_and_shows_values():
    text = session_output([
        "let x = 21", "x * 2", '"quoted"', "fn sq(n: Int) -> Int {", "  n * n", "}", "sq(9)",
        "var xs: List[Int] = []", "xs.push(3)", "xs", ":quit"])
    assert ">>> 42\n" in text and '"quoted"' in text and "81" in text and "[3]" in text
    assert "... " in text      # the function continued over several lines


def test_repl_errors_leave_no_trace():
    text = session_output(['let bad: Int = "s"', "bad", "[1][5]", "1 + 1"])
    assert "expected Int, found Str" in text
    assert "'bad' isn't defined" in text
    assert "runtime error: index 5" in text
    assert text.rstrip().endswith(">>> 2\n>>>") or ">>> 2" in text


def test_repl_commands():
    text = session_output([":type [1].map(fn(v) { v > 0 })", "let a = 1", ":reset", "a", ":help", ":bogus"])
    assert "[1].map(fn(v) { v > 0 }) : List[Bool]" in text
    assert "(everything forgotten)" in text and "'a' isn't defined" in text
    assert ":type EXPR" in text and "unknown command :bogus" in text


def test_repl_allows_redefining_names():
    text = session_output(["let x = 1", 'let x = "two"', "x"])
    assert '"two"' in text and "already defined" not in text
