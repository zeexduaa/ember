import io

import pytest

from ember.driver import run_file
from ember.source import EmberError


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def run(path):
    out = io.StringIO()
    run_file(path, out=out)
    return out.getvalue()


def errors(path):
    with pytest.raises(EmberError) as exc:
        run(path)
    return [d.message for d in exc.value.all_diagnostics()], exc.value.render()


GEOMETRY = """
pub let PI = 3.0
var calls = 0
pub struct Point { x: Float, y: Float }
pub enum Shape { Circle(Float), Square(Float) }
impl Point {
    fn origin() -> Point { Point { x: 0.0, y: 0.0 } }
    fn plus(self, o: Point) -> Point { Point { x: self.x + o.x, y: self.y + o.y } }
}
pub fn area(s: Shape) -> Float {
    calls += 1
    match s { Circle(r) => PI * r * r, Square(a) => a * a }
}
pub fn calls_so_far() -> Int { calls }
fn hidden() -> Int { 1 }
print("geometry runs once")
"""


def test_imported_types_functions_and_values(tmp_path):
    write(tmp_path / "geometry.em", GEOMETRY)
    write(tmp_path / "other.em", 'import "geometry"\npub fn twice(s: geometry.Shape) -> Float { geometry.area(s) * 2.0 }')
    main = write(tmp_path / "main.em", """
import "geometry"
import "other" as o
let p = geometry.Point { x: 1.0, y: 2.0 }.plus(geometry.Point.origin())
let s: geometry.Shape = geometry.Shape.Square(3.0)
let kind = match s { Circle(_) => "circle", Shape.Square(_) => "square" }
print(p, geometry.area(s), o.twice(s), kind, geometry.PI, geometry.calls_so_far())
""")
    assert run(main) == "geometry runs once\nPoint { x: 1.0, y: 2.0 } 9.0 18.0 square 3.0 2\n"


def test_paths_are_relative_to_the_importing_file(tmp_path):
    write(tmp_path / "lib" / "deep" / "util.em", 'pub fn hi() -> Str { "hi" }')
    write(tmp_path / "lib" / "helpers.em", 'import "deep/util"\npub fn hello() -> Str { util.hi() + "!" }')
    main = write(tmp_path / "app" / "main.em", 'import "../lib/helpers" as h\nprint(h.hello())')
    assert run(main) == "hi!\n"


def test_standard_library(tmp_path):
    main = write(tmp_path / "main.em", 'import "math"\nimport "text"\n'
                                       'print(math.gcd(12, 18), math.is_prime(7), text.capitalize("ember"))')
    assert run(main) == "6 true Ember\n"


def test_a_local_file_shadows_the_standard_library(tmp_path):
    write(tmp_path / "math.em", "pub fn gcd(a: Int, b: Int) -> Int { 42 }")
    main = write(tmp_path / "main.em", 'import "math"\nprint(math.gcd(1, 2))')
    assert run(main) == "42\n"


def test_only_pub_declarations_are_visible(tmp_path):
    write(tmp_path / "geometry.em", GEOMETRY)
    main = write(tmp_path / "main.em", 'import "geometry"\nprint(geometry.hidden())')
    messages, _ = errors(main)
    assert messages == ["module geometry has no public member 'hidden'"]


def test_missing_modules_and_cycles(tmp_path):
    main = write(tmp_path / "main.em", 'import "nowhere"')
    messages, text = errors(main)
    assert messages == ['can\'t find a module "nowhere"'] and "main.em:1:8" in text
    write(tmp_path / "a.em", 'import "b"')
    write(tmp_path / "b.em", 'import "a"')
    messages, text = errors(tmp_path / "a.em")
    assert messages[0] == "import cycle: a.em -> b.em -> a.em"
    assert "b.em:1:8" in text


def test_errors_inside_an_imported_file_point_into_it(tmp_path):
    write(tmp_path / "broken.em", 'pub fn f() -> Int { "no" }')
    main = write(tmp_path / "main.em", 'import "broken"\nprint(broken.f())')
    messages, text = errors(main)
    assert messages == ["wrong return type: expected Int, found Str", "can't import \"broken\" because of the errors above"]
    assert "broken.em:1:21" in text and "main.em:1:8" in text


def test_imports_need_distinct_names(tmp_path):
    write(tmp_path / "a" / "util.em", "pub let x = 1")
    write(tmp_path / "b" / "util.em", "pub let x = 2")
    main = write(tmp_path / "main.em", 'import "a/util"\nimport "b/util"')
    messages, _ = errors(main)
    assert messages == ["'util' is already defined; import it with another name: import \"b/util\" as other"]
    main = write(tmp_path / "main.em", 'import "a/util"\nimport "b/util" as util2\nprint(util.x, util2.x)')
    assert run(main) == "1 2\n"


def test_callbacks_into_another_module_use_its_globals(tmp_path):
    write(tmp_path / "counter.em", "var n = 0\npub fn bump(x: Int) -> Int {\n  n += 1\n  x + n\n}")
    main = write(tmp_path / "main.em", 'import "counter"\nlet n = 100\nprint([1, 1, 1].map(counter.bump), n)')
    assert run(main) == "[2, 3, 4] 100\n"


def test_public_type_aliases_can_be_imported(tmp_path):
    write(tmp_path / "grid.em", "pub type Grid = List[List[Int]]\npub fn make() -> Grid { [[1, 2], [3]] }")
    main = write(tmp_path / "main.em", 'import "grid"\nlet g: grid.Grid = grid.make()\nprint(g.len(), g[0])')
    assert run(main) == "2 [1, 2]\n"
