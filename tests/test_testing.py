import io

from ember.cli import main
from ember.testing import run_tests


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_runner_reports_passes_failures_and_crashes(tmp_path):
    write(tmp_path / "demo_test.em", """
var setup_runs = 0
setup_runs += 1
fn test_good() { assert_eq(1 + 1, 2) }
fn test_bad() { assert_eq([1, 2].reversed(), [1, 2], "order") }
fn test_crash() {
    let xs: List[Int] = []
    print(xs[0])
}
fn test_setup_ran_once() { assert_eq(setup_runs, 1) }
fn helper() -> Int { 1 }
""")
    out = io.StringIO()
    code = run_tests([str(tmp_path)], out=out)
    text = out.getvalue()
    assert code == 1
    assert "  ok    test_good" in text and "  ok    test_setup_ran_once" in text
    assert "  FAIL  test_bad" in text and "assert_eq failed: [2, 1] != [1, 2] (order)" in text
    assert "  FAIL  test_crash" in text and "index 0 is out of range" in text
    assert "helper" not in text
    assert "4 test(s): 2 passed, 2 failed" in text


def test_filter_and_files_that_dont_compile(tmp_path):
    write(tmp_path / "a_test.em", "fn test_one() { }\nfn test_two() { assert(false) }")
    write(tmp_path / "b_test.em", "fn test_x() { nope() }")
    out = io.StringIO()
    assert run_tests([str(tmp_path / "a_test.em")], pattern="one", out=out) == 0
    assert "1 test(s): 1 passed, 0 failed" in out.getvalue()
    out = io.StringIO()
    assert run_tests([str(tmp_path)], out=out) == 1
    assert "could not run" in out.getvalue() and "'nope' isn't defined" in out.getvalue()
    assert "1 file(s) couldn't run" in out.getvalue()


def test_no_test_files(tmp_path, capsys):
    assert main(["test", str(tmp_path)]) == 1
    assert "no test files found" in capsys.readouterr().out


def test_the_example_tests_pass(capsys):
    assert main(["test", "examples"]) == 0
