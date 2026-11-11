import pytest

from ember.checker import check
from ember.parser import parse
from ember.source import EmberError, Source
from ember.types import resolve


def checked(text):
    return check(parse(text), Source(text))


def type_of(text, name):
    return str(resolve(checked(text).globals.names[name].type))


def errors(text):
    with pytest.raises(EmberError) as exc:
        checked(text)
    return [d.message for d in exc.value.diagnostics]


def error(text):
    found = errors(text)
    assert len(found) == 1, found
    return found[0]


def warnings(text):
    return [w.message for w in checked(text).warnings]


# -- inference ---------------------------------------------------------------------


@pytest.mark.parametrize("expr, expected", [
    ("1 + 2", "Int"), ("1.5 * 2.0", "Float"), ('"a" + "b"', "Str"), ("[1, 2] + [3]", "List[Int]"),
    ("1 < 2 and not false", "Bool"), ("0..10", "Range"), ('{"a": [1]}', "Map[Str, List[Int]]"),
    ('"n=${1 + 1}"', "Str"), ("[1, 2].map(fn(x) { x > 1 })", "List[Bool]"),
    ('["a"].map(fn(s) { s.len() }).sum()', "Int"), ("[3, 1].reduce(0, fn(acc, x) { acc + x })", "Int"),
    ("[1, 2].first()", "Int?"), ("[1].first() ?? 0", "Int"), ('{"a": 1}.get("a")', "Int?"),
    ("if true { 1 } else { 2 }", "Int"), ("if true { 1 } else { nil }", "Int?"),
    ("fn(x: Int) { x * 2 }", "fn(Int) -> Int"), ("fn() { }", "fn() -> Nil"),
    ('"a,b".split(",")', "List[Str]"), ("[1.5, 2.5].sorted()", "List[Float]"),
])
def test_expression_types(expr, expected):
    assert type_of(f"let v = {expr}", "v") == expected


def test_empty_collections_learn_their_type_from_use():
    assert type_of("var xs = []\nxs.push(1)", "xs") == "List[Int]"
    assert type_of('var m = {}\nm["a"] = [true]', "m") == "Map[Str, List[Bool]]"
    assert type_of("let xs: List[Str] = []", "xs") == "List[Str]"
    assert error("let xs = []") == "can't tell the type of this empty list"


def test_generic_functions_are_instantiated_per_call():
    text = """
fn first[T](xs: List[T]) -> T? {
    if xs.is_empty() { return nil }
    xs[0]
}
let a = first([1, 2])
let b = first(["x"])
let c = first([[1.5]])
"""
    assert [type_of(text, n) for n in "abc"] == ["Int?", "Str?", "List[Float]?"]
    assert error("fn f[T](x: T) -> T { x + 1 }") == "'+' needs two values of the same type, found T and Int"


def test_structs_enums_and_methods():
    text = """
struct Point { x: Float, y: Float }
enum Shape { Circle(Float), Rect(Float, Float), Empty }
impl Point {
    fn len(self) -> Float { sqrt(self.x * self.x + self.y * self.y) }
    fn origin() -> Point { Point { x: 0.0, y: 0.0 } }
}
impl Shape {
    fn area(self) -> Float {
        match self { Circle(r) => 3.14 * r * r, Rect(w, h) => w * h, Empty => 0.0 }
    }
}
let p = Point.origin()
let d = p.len()
let s = Shape.Circle(1.0)
let a = s.area()
let e = Shape.Empty
let make = Shape.Rect
"""
    assert [type_of(text, n) for n in ("p", "d", "s", "a", "e", "make")] == [
        "Point", "Float", "Shape", "Float", "Shape", "fn(Float, Float) -> Shape"]


def test_mutual_recursion_and_use_before_declaration():
    text = """
fn even(n: Int) -> Bool { if n == 0 { true } else { odd(n - 1) } }
fn odd(n: Int) -> Bool { if n == 0 { false } else { even(n - 1) } }
let v = even(10)
"""
    assert type_of(text, "v") == "Bool"


def test_if_let_and_match_narrow_optionals():
    text = """
let m = {"a": 1}
var total = 0
if let n = m.get("a") { total += n }
let x: Int? = nil
let y = match x { nil => 0, n => n + 1 }
"""
    assert type_of(text, "y") == "Int"


def test_closures_capture_outer_variables():
    text = """
fn counter() -> fn() -> Int {
    var n = 0
    fn() { n += 1
           n }
}
let c = counter()
let v = c()
"""
    assert type_of(text, "v") == "Int"


# -- errors ---------------------------------------------------------------------------


@pytest.mark.parametrize("text, message", [
    ('let a: Int = "hi"', "expected Int, found Str"),
    ("let b = 1 + 2.0", "'+' needs two values of the same type, found Int and Float"),
    ("let c = 1\nc = 2", "can't assign to 'c': it was declared with let"),
    ("let d = nope + 1", "'nope' isn't defined"),
    ("struct P { x: Int }\nlet e = P { x: 1, y: 2 }", "P has no field 'y'"),
    ("struct P { x: Int }\nlet e = P { }", "missing field x in P"),
    ("let m: Int? = nil\nlet g = m + 1", "'+' needs two values of the same type, found Int? and Int"),
    ("let h = [1].mapp(fn(x) { x })", "List[Int] has no method 'mapp'"),
    ("var j = 0\nj = \"s\"", "expected Int, found Str"),
    ('let k = if true { 1 } else { "one" }', "the branches have different types: Int and Str"),
    ("let n = 1 == \"1\"", "can't compare Int with Str"),
    ("let o = 5 == nil", "an Int is never nil"),
    ("fn f(x: Int) -> Int { if x > 0 { return 1 } }", "'f' must return an Int on every path"),
    ("fn f() -> Str { 1 }", "wrong return type: expected Str, found Int"),
    ("fn f(x: Int) { x = 2 }", "can't assign to the parameter 'x'"),
    ("let f = 1\nf(2)", "an Int can't be called"),
    ("fn f(a: Int) { }\nf(1, 2)", "'f' takes 1 argument, but 2 were given"),
    ("let x: Int? = nil\nx.abs()", "can't call .abs() on a value that might be nil (Int?)"),
    ("break", "'break' outside a loop"),
    ("return 1", "'return' outside a function"),
    ("for x in 5 { }", "can't loop over an Int"),
    ("let x = nil", "can't tell what type 'x' should have from nil"),
    ("let s = \"abc\"\ns[0] = \"x\"", "strings can't be changed in place"),
    ("let x = 1\nlet x = 2", "'x' is already defined in this scope"),
    ("let m: Map[List[Int], Int] = {}", "map keys must be Int, Str or Bool, not List[Int]"),
    ("struct P { x: Int }\nlet p = P", "'P' is a struct, not a value"),
    ("let v = [\"a\"].sum()", "this needs a list of Ints or Floats, not List[Str]"),
    ("let v = [1].join(\",\")", "this needs a list of Strs (convert with .map(str) first), not List[Int]"),
    ("let a = 1 < \"x\"", "'<' can't compare Int and Str"),
    ("let t: Bogus = 1", "unknown type 'Bogus'"),
    ("let i = 99999999999999999999", "integer is too big (Int is 64-bit)"),
    ("let v = min(1)", "'min' takes 2 arguments, but 1 were given"),
    ("let f = print", "'print' must be called directly: print(...)"),
])
def test_error_messages(text, message):
    assert message in errors(text)


def test_errors_come_with_helpful_notes():
    with pytest.raises(EmberError) as exc:
        checked("let x: Float = 1")
    assert exc.value.diagnostics[0].notes == [
        "convert with float(...), or write the number with a decimal point (1.0)"]
    with pytest.raises(EmberError) as exc:
        checked("let count = 1\nlet y = cuont")
    assert exc.value.diagnostics[0].notes == ["did you mean 'count'?"]


def test_all_errors_are_reported_in_source_order():
    found = errors("fn f() -> Int { \"x\" }\nlet a: Int = true\nlet b = nope")
    assert found == ["wrong return type: expected Int, found Str", "expected Int, found Bool", "'nope' isn't defined"]


# -- match exhaustiveness ---------------------------------------------------------------

SHAPES = "enum Shape { Circle(Float), Rect(Float, Float), Empty }\n"


@pytest.mark.parametrize("arms, missing", [
    ("Circle(r) => 1", "Shape.Rect(_, _)"),
    ("Circle(_) => 1, Rect(_, _) => 2", "Shape.Empty"),
    ("Circle(r) if r > 0.0 => 1, Rect(_, _) => 2, Empty => 3", "Shape.Circle(_)"),
    ("Circle(1.0) => 1, Rect(_, _) => 2, Empty => 3", "Shape.Circle(another Float)"),
])
def test_missing_cases_are_named(arms, missing):
    message = error(SHAPES + f"fn f(s: Shape) -> Int {{ match s {{ {arms} }} }}")
    assert message == f"this match doesn't cover every case: {missing} isn't handled"


@pytest.mark.parametrize("subject_type, arms", [
    ("Shape", "Circle(_) => 1, Rect(_, _) => 2, Empty => 3"),
    ("Shape", "Empty => 1, _ => 2"),
    ("Bool", "true => 1, false => 2"),
    ("Int?", "nil => 0, n => n"),
    ("Shape?", "nil => 0, Circle(_) => 1, Rect(_, _) => 2, Empty => 3"),
    ("Int", "0 => 1, n => n"),
])
def test_exhaustive_matches(subject_type, arms):
    checked(SHAPES + f"fn f(s: {subject_type}) -> Int {{ match s {{ {arms} }} }}")


def test_non_exhaustive_simple_types():
    assert "true isn't handled" in error("fn f(b: Bool) -> Int { match b { false => 1 } }")
    assert "nil isn't handled" in error("fn f(b: Int?) -> Int { match b { 1 => 1, 2 => 2 } }") or \
        "another Int" in error("fn f(b: Int?) -> Int { match b { nil => 0, 2 => 2 } }")
    assert "another Str" in error('fn f(s: Str) -> Int { match s { "a" => 1 } }')


def test_pattern_errors():
    assert error(SHAPES + "fn f(s: Shape) { match s { Square => 1, _ => 2 } }") == "Shape has no variant 'Square'"
    assert error(SHAPES + "fn f(s: Shape) { match s { Circle => 1, _ => 2 } }") == "Circle has 1 field; write Circle(_)"
    assert error(SHAPES + "fn f(s: Shape) { match s { Rect(a) => 1, _ => 2 } }") == \
        "Rect has 2 fields, but the pattern has 1"
    assert error("fn f(n: Int) { match n { Some(x) => 1, _ => 2 } }") == \
        "'Some' is a variant pattern, but the value is an Int"


# -- warnings -------------------------------------------------------------------------------


def test_warnings():
    assert "'unused' is never used" in warnings("fn f() { let unused = 1 }")
    assert warnings("fn f() { let _ok = 1 }") == []
    assert "this arm is never reached: an earlier arm matches everything" in warnings(
        "fn f(n: Int) -> Int { match n { x => 1, 0 => 2 } }")
    assert "this value isn't used" in warnings("fn f(a: Int) { a + 1 }")
    assert "the left side of '??' is never nil (it's an Int)" in warnings("let v = 1 ?? 2")


def test_exhaustiveness_terminates_on_recursive_enums():
    enum = "enum E { Num(Int), Add(E, E), Neg(E) }\n"
    checked(enum + "fn f(e: E) -> Int { match e { Num(n) => n, Add(Num(0), b) => 0, Add(a, b) => 1, Neg(x) => 2 } }")
    # Add(Num(0), _) is covered, so the example is an Add whose left side is another number.
    assert error(enum + "fn f(e: E) -> Int { match e { Num(n) => n, Add(Num(0), b) => 0, Neg(_) => 2 } }") == \
        "this match doesn't cover every case: E.Add(E.Num(another Int), _) isn't handled"
    message = error(enum + "fn f(e: E) -> Int { match e { Num(n) => n, Add(_, _) => 1 } }")
    assert message == "this match doesn't cover every case: E.Neg(_) isn't handled"


def test_operator_on_an_optional_suggests_unwrapping():
    with pytest.raises(EmberError) as exc:
        checked('fn f(n: Int?) -> Int { n + 1 }')
    assert exc.value.diagnostics[0].notes == ["one side might be nil; unwrap it first with `if let`, `??` or `match`"]
