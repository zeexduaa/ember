import io

import pytest

from ember.driver import dis_source, run_source
from ember.vm import RuntimeFailure


def run(text, stdin=""):
    out = io.StringIO()
    run_source(text, "test.em", out=out, stdin=io.StringIO(stdin))
    return out.getvalue()


def lines(text, stdin=""):
    return run(text, stdin).splitlines()


def failure(text):
    with pytest.raises(RuntimeFailure) as exc:
        run(text)
    return exc.value


# -- values and operators ---------------------------------------------------------------


@pytest.mark.parametrize("expr, shown", [
    ("1 + 2 * 3", "7"), ("7 / 2", "3"), ("-7 / 2", "-3"), ("-7 % 3", "-1"), ("7 % -3", "1"),
    ("7.0 / 2.0", "3.5"), ("-7.5 % 2.0", "-1.5"), ("2.0 * 3.0", "6.0"), ("0.1 + 0.2", "0.30000000000000004"),
    ('"a" + "b"', "ab"), ("[1] + [2, 3]", "[1, 2, 3]"), ("not true or false", "false"),
    ("1 < 2 and 2 <= 2", "true"), ('"apple" < "banana"', "true"), ("[[1], [2]] == [[1], [2]]", "true"),
    ('{"a": 1} == {"a": 1}', "true"), ("nil", "nil"), ('["x", "y"]', '["x", "y"]'),
    ('{"k": [1.5]}', '{"k": [1.5]}'), ("0..3", "0..3"), ('"tab\\there"', "tab\there"),
    ('str(1.0) + str(2)', "1.02"), ("1_000_000", "1000000"), ("0xff", "255"),
])
def test_expressions(expr, shown):
    assert run(f"print({expr})") == shown + "\n"


def test_short_circuit():
    text = """
var calls = 0
fn side() -> Bool {
    calls += 1
    true
}
let a = false and side()
let b = true or side()
let c: Int? = 5
let d = c ?? (if side() { 1 } else { 2 })
print(calls, a, b, d)
"""
    assert run(text) == "0 false true 5\n"


def test_templates():
    assert run('let n = 3\nlet xs = [1]\nprint("n=${n} xs=${xs} sum=${n + 1} s=${"in"} nil=${nil}")') == \
        "n=3 xs=[1] sum=4 s=in nil=nil\n"


# -- control flow --------------------------------------------------------------------------


def test_loops_break_and_continue():
    text = """
var out: List[Int] = []
for i in 0..20 {
    if i % 3 == 0 { continue }
    if i > 10 { break }
    out.push(i)
}
var n = 0
while true {
    n += 1
    if n == 5 { break }
}
for i in 1..=3 { out.push(i * 100) }
print(out, n)
"""
    assert run(text) == "[1, 2, 4, 5, 7, 8, 10, 100, 200, 300] 5\n"


def test_iterating_strings_maps_and_a_changing_list():
    text = """
var s = ""
for ch in "abc" { s = ch + s }
let m = {"x": 1, "y": 2}
var keys: List[Str] = []
for k in m { keys.push(k) }
var xs = [1, 2]
for x in xs { xs.push(x * 10) }
print(s, keys, xs)
"""
    assert run(text) == 'cba ["x", "y"] [1, 2, 10, 20]\n'


def test_if_and_match_as_expressions():
    text = """
fn sign(n: Int) -> Str { if n < 0 { "neg" } else if n == 0 { "zero" } else { "pos" } }
fn describe(n: Int?) -> Str {
    match n {
        nil => "nothing"
        0 => "zero"
        x if x < 0 => "negative ${x}"
        x => "positive ${x}"
    }
}
print(sign(-2), sign(0), sign(9))
print(describe(nil), describe(0), describe(-4), describe(7))
"""
    assert lines(text) == ["neg zero pos", "nothing zero negative -4 positive 7"]


def test_nested_patterns():
    text = """
enum Expr { Num(Int), Add(Expr, Expr), Mul(Expr, Expr), Neg(Expr) }
fn eval(e: Expr) -> Int {
    match e {
        Num(n) => n
        Add(Num(0), b) => eval(b)
        Add(a, b) => eval(a) + eval(b)
        Mul(_, Num(0)) => 0
        Mul(a, b) => eval(a) * eval(b)
        Neg(inner) => -eval(inner)
    }
}
let e = Expr.Add(Expr.Num(2), Expr.Mul(Expr.Num(3), Expr.Neg(Expr.Num(4))))
print(eval(e), eval(Expr.Add(Expr.Num(0), Expr.Num(9))), eval(Expr.Mul(e, Expr.Num(0))))
"""
    assert run(text) == "-10 9 0\n"


def test_if_let():
    text = """
let m = {"a": 1}
if let v = m.get("a") { print("found", v) } else { print("missing") }
if let v = m.get("b") { print("found", v) } else { print("missing") }
"""
    assert lines(text) == ["found 1", "missing"]


def test_early_return_from_loops():
    text = """
fn find(xs: List[Int], target: Int) -> Int? {
    for i in 0..xs.len() {
        if xs[i] == target { return i }
    }
    nil
}
print(find([5, 6, 7], 7), find([5], 1))
"""
    assert run(text) == "2 nil\n"


# -- functions and closures -------------------------------------------------------------------


def test_recursion_and_mutual_recursion():
    text = """
fn fact(n: Int) -> Int { if n <= 1 { 1 } else { n * fact(n - 1) } }
fn even(n: Int) -> Bool { if n == 0 { true } else { odd(n - 1) } }
fn odd(n: Int) -> Bool { if n == 0 { false } else { even(n - 1) } }
print(fact(20), even(10), odd(7))
"""
    assert run(text) == "2432902008176640000 true true\n"


def test_closures_share_and_keep_variables():
    text = """
fn make() -> List[fn() -> Int] {
    var n = 0
    let inc = fn() { n += 1
                     n }
    let get = fn() { n }
    [inc, get]
}
let fs = make()
fs[0]()
fs[0]()
print(fs[1]())
var later: List[fn() -> Int] = []
for i in 0..3 { later.push(fn() { i }) }
var j = 0
while j < 3 {
    let k = j
    later.push(fn() { k * 10 })
    j += 1
}
print(later.map(fn(f) { f() }))
"""
    assert lines(text) == ["2", "[0, 1, 2, 0, 10, 20]"]


def test_nested_functions_and_recursion_inside():
    text = """
fn outer(n: Int) -> Int {
    fn helper(k: Int) -> Int { if k == 0 { 0 } else { k + helper(k - 1) } }
    helper(n)
}
print(outer(10))
"""
    assert run(text) == "55\n"


def test_higher_order_builtins():
    text = """
let xs = [5, 3, 8, 1]
print(xs.filter(fn(x) { x > 2 }), xs.sorted(), xs.reduce(0, fn(a, x) { a + x }))
print(xs.any(fn(x) { x > 7 }), xs.all(fn(x) { x > 0 }), xs.count(fn(x) { x % 2 == 1 }))
print(xs.find(fn(x) { x > 4 }), xs.find(fn(x) { x > 100 }))
let words = ["pear", "fig", "banana"]
print(words.sort_by(fn(w) { w.len() }), words.min(), words.max())
"""
    assert lines(text) == ["[5, 3, 8] [1, 3, 5, 8] 17", "true true 3", "5 nil",
                           '["fig", "pear", "banana"] banana pear']


# -- structs, enums, methods -------------------------------------------------------------------


def test_structs_are_shared_references():
    text = """
struct Counter { n: Int }
impl Counter {
    fn bump(self) { self.n += 1 }
    fn new() -> Counter { Counter { n: 0 } }
}
let a = Counter.new()
let b = a
b.bump()
a.bump()
print(a.n, b.n, a == b, a)
"""
    assert run(text) == "2 2 true Counter { n: 2 }\n"


def test_struct_fields_are_evaluated_in_written_order():
    text = """
struct P { a: Int, b: Int }
var log: List[Str] = []
fn note(s: Str, v: Int) -> Int {
    log.push(s)
    v
}
let p = P { b: note("b", 2), a: note("a", 1) }
print(log, p)
"""
    assert run(text) == '["b", "a"] P { a: 1, b: 2 }\n'


def test_enum_methods_and_variant_functions():
    text = """
enum Light { Red, Yellow, Green }
impl Light {
    fn next(self) -> Light {
        match self { Red => Light.Green, Green => Light.Yellow, Yellow => Light.Red }
    }
}
let make = Light.Red
print(make, make.next(), make.next().next())
enum Shape { Circle(Float), Rect(Float, Float) }
let builder = Shape.Rect
print(builder(1.0, 2.0), [1.0, 2.0].map(Shape.Circle))
"""
    assert lines(text) == ["Light.Red Light.Green Light.Yellow",
                           "Shape.Rect(1.0, 2.0) [Shape.Circle(1.0), Shape.Circle(2.0)]"]


def test_compound_assignment_to_elements_and_fields():
    text = """
struct Acc { total: Float }
var xs = [1, 2, 3]
xs[1] += 10
xs[-1] *= 2
let m = {"a": 1}
m["a"] -= 5
m["b"] = 7
let acc = Acc { total: 1.5 }
acc.total *= 2.0
print(xs, m, acc.total)
"""
    assert run(text) == '[1, 12, 6] {"a": -4, "b": 7} 3.0\n'


# -- built-ins ------------------------------------------------------------------------------


def test_string_and_map_methods():
    text = """
let s = "  Hello, World  "
print(s.trim().lower(), s.trim().split(", "), "a-b".replace("-", "+"), "ab".repeat(3))
print("hello".slice(1, 3), "hello".index_of("l"), "hello".chars().len(), "x".is_empty())
let m = {"b": 2, "a": 1}
print(m.keys(), m.values(), m.has("a"), m.get_or("z", 0), m.remove("b"), m)
"""
    assert lines(text) == ['hello, world ["Hello", "World"] a+b ababab',
                           'el 2 5 false', '["b", "a"] [2, 1] true 0 2 {"a": 1}']


def test_numbers():
    text = """
print(int(3.99), int(-3.99), round(2.5), round(-2.5), floor(-0.5), ceil(0.1))
print(float(3), sqrt(16.0), pow(2.0, 10.0), (-5).abs(), 2.pow(10), min(3, 1), max("a", "b"))
print(parse_int("42"), parse_int("4x"), parse_float("1.5"), parse_int(" 7 "))
"""
    assert lines(text) == ["3 -3 3 -2 -1 1", "3.0 4.0 1024.0 5 1024 1 b", "42 nil 1.5 7"]


def test_input():
    text = """
var total = 0
while true {
    let line = input("> ")
    if let text = line {
        total += parse_int(text) ?? 0
    } else {
        break
    }
}
print("total", total)
"""
    assert run(text, stdin="1\n2\nx\n3\n") == "> > > > > total 6\n"


# -- runtime errors -----------------------------------------------------------------------------


@pytest.mark.parametrize("text, message", [
    ("print([1, 2][5])", "index 5 is out of range for a list of length 2"),
    ('print("ab"[2])', "index 2 is out of range for a string of length 2"),
    ("let b = 0\nprint(1 / b)", "division by zero"),
    ("let b = 0\nprint(1 % b)", "division by zero (in %)"),
    ("let b = 0.0\nprint(1.0 / b)", "division by zero"),
    ("var x = 9223372036854775807\nx += 1", "integer overflow (Int is 64-bit)"),
    ("print(2.pow(70))", "integer overflow (Int is 64-bit)"),
    ('let m = {"a": 1}\nprint(m["b"])', 'key "b" isn\'t in the map (use .get(key) for an optional value)'),
    ('panic("custom")', "custom"),
    ('assert(false, "nope")', "nope"),
    ("print(sqrt(-1.0))", "sqrt of a negative number (-1.0)"),
    ('print("abc".slice(2, 1))', "slice(2, 1) is out of range for length 3"),
    ("var xs = [1]\nxs[3] = 1", "index 3 is out of range for a list of length 1"),
])
def test_runtime_errors(text, message):
    assert failure(text).message == message


def test_stack_traces_name_functions_and_lines():
    err = failure("fn inner(xs: List[Int]) -> Int { xs[9] }\nfn outer() -> Int {\n  inner([])\n}\nouter()")
    assert err.trace == ["inner (test.em:1)", "outer (test.em:3)", "<main> (test.em:5)"]


def test_runaway_recursion_is_reported_compactly():
    err = failure("fn f(n: Int) -> Int { f(n + 1) }\nf(0)")
    assert "stack overflow" in err.message
    text = err.render()
    assert "(and 1998 more times)" in text and len(text.splitlines()) == 3


def test_errors_inside_callbacks_show_the_lambda():
    err = failure("print([1, 0].map(fn(x) { 10 / x }))")
    assert err.trace[0].startswith("<lambda>") and err.trace[-1].startswith("<main>")


def test_disassembler_lists_nested_functions():
    listing = dis_source("fn add(a: Int, b: Int) -> Int { a + b }\nprint(add(1, 2))")
    assert "== <main>" in listing and "== add (arity 2, 2 locals) ==" in listing
    assert "IADD" in listing and "CALL_BUILTIN" in listing


@pytest.mark.parametrize("expr", ["m / -1", "m.abs()", "-m", "m % -1", "[9223372036854775807, 1].sum()", "2.pow(63)"])
def test_every_overflow_is_caught(expr):
    assert failure(f"let m = -9223372036854775807 - 1\nprint({expr})").message == "integer overflow (Int is 64-bit)"


def test_structs_that_contain_themselves_print_and_compare():
    text = """
struct N { v: Int, next: List[N] }
let a = N { v: 1, next: [] }
a.next.push(a)
let b = N { v: 1, next: [] }
b.next.push(b)
let c = N { v: 2, next: [] }
c.next.push(c)
print(a)
print(a == b, a == c, [a].contains(b), "${a.v}")
"""
    assert lines(text) == ["N { v: 1, next: [...] }", "true false true 1"]


def test_negative_zero_keeps_its_sign():
    assert run("print(-0.0, 0.0 * -1.0, 0.0)") == "-0.0 -0.0 0.0\n"


def test_programs_get_a_large_thread_stack():
    import threading
    from ember.vm import THREAD_STACK
    old = threading.stack_size()
    try:
        threading.stack_size(THREAD_STACK)   # must be accepted on every platform (Windows needs < 256 MiB)
    finally:
        threading.stack_size(old)


def test_deep_recursion_through_callbacks_reaches_embers_own_limit():
    text = """
fn depth(n: Int) -> Int {
    if n == 0 { return 0 }
    [n].map(fn(x) { depth(x - 1) })[0] + 1
}
print(depth(900))
"""
    assert run(text) == "900\n"      # used to stop at about 150, on Python's recursion limit
    err = failure("fn d(n: Int) -> Int { [n].map(fn(x) { d(x + 1) })[0] }\nprint(d(0))")
    assert err.message.startswith("stack overflow: more than 2000 calls deep")
    shown = err.render()
    assert "(these 2 lines repeat" in shown and len(shown.splitlines()) < 10


def test_interrupting_a_running_program():
    import threading
    from ember.driver import compile_source, make_vm
    compiled = compile_source("var n = 0\nwhile true { n += 1 }")
    vm = make_vm(compiled)
    threading.Timer(0.3, lambda: setattr(vm, "interrupted", True)).start()
    with pytest.raises(RuntimeFailure) as exc:
        vm.run_main(compiled.proto)
    assert exc.value.message == "interrupted"


def test_sum_of_an_empty_float_list_is_a_float():
    assert run("let xs: List[Float] = []\nprint(xs.sum(), [1.5, 2.0].sum(), [1, 2].sum())") == "0.0 3.5 3\n"


def test_wrapping_checked_and_saturating_arithmetic():
    text = """
let max = 9223372036854775807
let min = -max - 1
print(max.wrapping_add(1) == min, min.wrapping_sub(1) == max, max.wrapping_mul(2))
print(max.checked_add(1), 5.checked_add(1), 7.checked_div(0), min.checked_div(-1), (-7).checked_div(2))
print(max.saturating_add(10) == max, min.saturating_sub(1) == min, 3.saturating_mul(4))
"""
    assert lines(text) == ["true true -2", "nil 6 nil nil -3", "true true 12"]


def test_same_compares_identity():
    text = """
struct Acct { balance: Int }
let a = Acct { balance: 5 }
let b = a
let c = Acct { balance: 5 }
let xs = [1]
print(a == c, same(a, b), same(a, c), same(xs, xs), same(xs, [1]))
"""
    assert run(text) == "true true false true false\n"


def test_range_and_or_patterns():
    text = """
fn grade(n: Int) -> Str {
    match n {
        90..=100 => "A"
        80..90 => "B"
        0 | 1 | 2 => "low"
        -100..0 => "negative"
        _ => "other"
    }
}
enum Light { Red, Yellow, Green }
enum E { A(Int), B }
let maybe: Int? = nil
print([95, 90, 89, 80, 1, -5, 79, 100, 101].map(grade))
print(match "Yes".lower() { "y" | "yes" => true, _ => false }, match Light.Yellow { Red | Yellow => 1, Green => 2 })
print(match maybe { 1..=5 => "small", nil => "none", _ => "big" }, match E.A(3) { A(1..=5) | B => "hit", A(_) => "miss" })
"""
    assert lines(text) == ['["A", "A", "B", "B", "low", "negative", "other", "A", "other"]', "true 1", "none hit"]


def test_struct_patterns():
    text = """
struct Point { x: Int, y: Int }
fn where_is(p: Point) -> Str {
    match p {
        Point { x: 0, y: 0 } => "origin"
        Point { x: 0, y } => "y axis at ${y}"
        Point { y: 0, .. } => "x axis"
        Point { x, y } if x == y => "diagonal"
        _ => "elsewhere"
    }
}
let a = Point { x: 0, y: 0 }
let b = Point { x: 0, y: 5 }
let c = Point { x: 3, y: 0 }
let d = Point { x: 2, y: 2 }
let e = Point { x: 1, y: 9 }
print(where_is(a), where_is(b), where_is(c), where_is(d), where_is(e))
enum Shape { Dot(Point), Line(Point, Point) }
fn kind(s: Shape) -> Str {
    match s {
        Dot(Point { x: 0, .. }) => "dot on the y axis"
        Dot(_) => "dot"
        Line(Point { x: x1, .. }, Point { x: x2, .. }) if x1 == x2 => "vertical"
        Line(_, _) => "line"
    }
}
print(kind(Shape.Dot(a)), kind(Shape.Line(c, Point { x: 3, y: 7 })))
let maybe: Point? = b
print(match maybe { nil => "none", Point { x: 0, .. } => "x is zero", _ => "other" })
"""
    assert lines(text) == ["origin y axis at 5 x axis diagonal elsewhere", "dot on the y axis vertical", "x is zero"]
