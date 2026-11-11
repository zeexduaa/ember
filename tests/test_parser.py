import pytest

from conftest import errors_of, sexpr
from ember import ast as A
from ember.parser import parse


def expr(text):
    stmt = parse(text).stmts[0]
    assert isinstance(stmt, A.ExprStmt), stmt
    return sexpr(stmt.expr)


@pytest.mark.parametrize("text, tree", [
    ("1 + 2 * 3", "(+ 1 (* 2 3))"),
    ("1 - 2 - 3", "(- (- 1 2) 3)"),
    ("-a * b", "(* (- a) b)"),
    ("not a == b", "(not (== a b))"),
    ("not a and b or c", "(or (and (not a) b) c)"),
    ("a ?? b ?? c", "(?? a (?? b c))"),
    ("a ?? b or c", "(?? a (or b c))"),
    ("0..n + 1", "(.. 0 (+ n 1))"),
    ("1..=10", "(..= 1 10)"),
    ("f(x)(y)[0].z", "(. (index (call (call f x) y) 0) z)"),
    ("xs.map(f).len()", "(call (. (call (. xs map) f) len))"),
    ("(1 + 2) * 3", "(* (+ 1 2) 3)"),
    ("[1, [2], []]", "[1 [2] []]"),
    ('{"a": 1, "b": 2}', "{'a':1 'b':2}"),
    ('"hi ${name}!"', "(template 'hi ' name '!')"),
    ("Point { x: 1.0, y }", "(struct Point x:1.0 y:y)"),
    ("geo.Point { x: 1 }", "(struct (. geo Point) x:1)"),
])
def test_expressions(text, tree):
    assert expr(text) == tree


def test_comparisons_and_ranges_dont_chain():
    assert errors_of(parse, "a < b < c") == ["'<' can't be chained after '<'; add parentheses"]
    assert errors_of(parse, "0..1..2") == ["'..' can't be chained after '..'; add parentheses"]


def test_struct_literal_is_not_parsed_in_conditions():
    prog = parse("if x == Origin { print(1) }")
    cond = prog.stmts[0].expr.cond
    assert sexpr(cond) == "(== x Origin)"
    prog = parse("if (x == Origin { a: 1 }) { }")
    assert sexpr(prog.stmts[0].expr.cond) == "(== x (struct Origin a:1))"


def test_declarations():
    prog = parse('''
import "lib/geometry"
import "util" as u
pub struct Point { x: Float, y: Float }
enum Shape { Circle(Float), Rect(Float, Float), Empty }
impl Point {
    fn len(self) -> Float { 0.0 }
    fn origin() -> Point { Point { x: 0.0, y: 0.0 } }
}
fn first[T](xs: List[T]) -> T? { nil }
let a: Map[Str, List[Int]] = {}
var f: fn(Int, Str) -> Bool? = g
''')
    imp, imp2, struct, enum, impl, fn, let, var = prog.stmts
    assert (imp.path, imp.alias, imp2.alias) == ("lib/geometry", "geometry", "u")
    assert struct.public and [f.name for f in struct.fields] == ["x", "y"]
    assert [(v.name, len(v.fields)) for v in enum.variants] == [("Circle", 1), ("Rect", 2), ("Empty", 0)]
    assert [(m.name, m.has_self) for m in impl.methods] == [("len", True), ("origin", False)]
    assert fn.type_params[0][0] == "T" and isinstance(fn.ret, A.OptionalType)
    assert let.type.name == "Map" and let.type.args[1].name == "List" and not let.mutable
    assert var.mutable and isinstance(var.type, A.FnType) and isinstance(var.type.ret, A.OptionalType)


def test_statements_and_control_flow():
    prog = parse('''
for i in 0..10 {
    if i == 2 { continue } else if i > 8 { break } else { total += i }
}
while running { step() }
xs[0] = 1
p.x -= 2
return
''')
    loop, wh, a1, a2, ret = prog.stmts
    assert isinstance(loop, A.For) and loop.var == "i"
    branch = loop.body.stmts[0].expr
    assert isinstance(branch.else_, A.IfExpr) and isinstance(branch.else_.else_, A.Block)
    assert isinstance(wh, A.While)
    assert (a1.op, a2.op) == ("=", "-=") and isinstance(a2.target, A.Field)
    assert isinstance(ret, A.Return) and ret.value is None


def test_else_on_the_next_line():
    prog = parse("if a {\n x\n}\nelse {\n y\n}")
    assert prog.stmts[0].expr.else_ is not None


def test_match_arms_and_patterns():
    prog = parse('''
match s {
    Shape.Circle(r) => r,
    Rect(w, _) if w > 0.0 => w
    Empty => { 0.0 }
    -1 => 1.0
    "x" => 2.0,
    nil => 3.0
    other => 4.0
}
''')
    arms = prog.stmts[0].expr.arms
    p = [a.pattern for a in arms]
    assert isinstance(p[0], A.VariantPat) and (p[0].enum, p[0].variant) == ("Shape", "Circle")
    assert isinstance(p[1].args[1], A.WildcardPat) and arms[1].guard is not None
    assert p[2].args is None and isinstance(arms[2].body, A.BlockExpr)
    assert isinstance(p[3], A.LitPat) and p[3].value.value == -1
    assert isinstance(p[5].value, A.NilLit) and isinstance(p[6], A.BindPat)


def test_if_let_and_lambdas():
    prog = parse("if let n = first(xs) { n }\nlet f = fn(x: Int) -> Int { x * 2 }")
    branch = prog.stmts[0].expr
    assert branch.let_name == "n" and sexpr(branch.cond) == "(call first xs)"
    lam = prog.stmts[1].value
    assert isinstance(lam, A.Lambda) and lam.params[0].name == "x"


def test_several_errors_are_reported():
    messages = errors_of(parse, "let = 1\nfn f(x) { }\nlet z = 2 2\nlet y = (1 + \nlet w = 3\n")
    assert messages == [
        "expected a variable name, found '='",
        "parameter 'x' needs a type, e.g. x: Int",
        "expected end of line after the statement, found int '2'",
        "expected an expression, found 'let'",   # the open ( carries on to the next line
    ]


@pytest.mark.parametrize("text, message", [
    ("let x", "'let x' needs a value: let x = ..."),
    ("1 = 2", "can only assign to a variable, an element (xs[i]) or a field (p.x)"),
    ("fn f() { struct S { a: Int } }", "structs are declared at the top level"),
    ("enum E { lower }", "variant names start with a capital letter: Lower"),
    ("fn f(self) { }", "'self' is only a parameter of methods in an impl block"),
    ("match x { }", "a match needs at least one arm"),
    ("struct S { a: Int, a: Int }", "two fields are named 'a'"),
    ("let x: = 1", "expected a type, found '='"),
    ("pub x = 1", "expected fn, struct, enum, type, let or var after 'pub', found name 'x'"),
])
def test_error_messages(text, message):
    assert errors_of(parse, text)[0] == message
