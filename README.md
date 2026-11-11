# ember

A small, statically typed programming language with type inference, enums and pattern matching, closures and modules. It compiles to bytecode for its own virtual machine. The whole implementation is pure Python with **no dependencies**: lexer, parser, type checker, compiler, VM, standard library and REPL.

```rust
enum Shape {
    Circle(Float)
    Rect(Float, Float)
}

fn area(s: Shape) -> Float {
    match s {
        Circle(r) => 3.14159 * r * r
        Rect(w, h) => w * h
    }
}

let shapes = [Shape.Circle(1.0), Shape.Rect(2.0, 3.0)]
let total = shapes.map(fn(s) { area(s) }).sum()     # the lambda's types are inferred
print("total area: ${total}")
```

> **Status:** the language is largely complete and tested, but still a work in progress. Generic structs, traits, tuples, a formatter, a language server and a faster VM are among the many things on the [roadmap](ROADMAP.md). Expect breaking changes before 1.0.

## Why it's interesting

The type checker catches mistakes before the program runs, and says what to do about them:

```text
$ ember check demo.em
error: this match doesn't cover every case: Shape.Rect(_, _) isn't handled
 --> demo.em:4:11
  |
4 |     match s {
  |           ^
  = note: add an arm for it, or a catch-all arm: _ => ...

error: '+' needs two values of the same type, found Str and Int?
  --> demo.em:10:5
   |
10 |     "count: " + n
   |     ^^^^^^^^^^^^^
   = note: one side might be nil; unwrap it first with `if let`, `??` or `match`
```

- **Type inference.** Local variables, lambda parameters, and the element types of empty `[]` and `{}` are worked out from how they're used. Function signatures are written out.
- **No null surprises.** A value that might be missing has type `T?` and must be unwrapped with `if let`, `??` or `match` before use.
- **Exhaustive `match`.** The checker names a missing case (`Shape.Rect(_, _)`), handles nested patterns and recursive enums, and warns about arms that can never run.
- **Generic functions.** `fn first[T](xs: List[T]) -> T?`, with the type parameters inferred at each call.
- **Real closures.** They capture variables by reference, and each loop iteration gets its own variable.
- **Errors you can act on.** Every error at once, in source order, with notes like "did you mean 'count'?" or "convert with float(...)". Runtime errors come with an ember stack trace.

## Install and run

Python 3.11 or newer. It isn't on PyPI yet:

```console
$ git clone https://github.com/zeexduaa/ember
$ cd ember
$ python -m pip install -e .
$ ember run examples/hello.em
Hello, world!
```

| Command | What it does |
| --- | --- |
| `ember run FILE` | Check, compile and run (`-q` hides warnings) |
| `ember check FILE...` | Report errors and warnings without running |
| `ember dis FILE` | Show the bytecode |
| `ember eval "CODE"` | Run a snippet |
| `ember test [PATH...]` | Run the `test_*` functions in `*_test.em` files (`-k TEXT` to pick some) |
| `ember repl` or `ember` | The interactive prompt (`:type`, `:reset`, `:help`) |

```text
$ ember
ember 0.1.0 - :help for help, Ctrl+D to leave
>>> let xs = [3, 1, 2]
>>> xs.sorted().map(fn(x) { x * 10 })
[10, 20, 30]
>>> :type xs.first()
xs.first() : Int?
```

## The language in one screen

```rust
import "math"                                 # modules: other files, or the standard library

let name = "ember"                            # immutable
var count = 0                                 # mutable
let scores: Map[Str, Int] = {}                # annotate when the type can't be inferred

struct Point { x: Float, y: Float }

impl Point {
    fn length(self) -> Float { sqrt(self.x * self.x + self.y * self.y) }
    fn origin() -> Point { Point { x: 0.0, y: 0.0 } }     # no self: a static method
}

fn find(xs: List[Int], target: Int) -> Int? {   # Int? means "an Int, or nil"
    for i in 0..xs.len() {
        if xs[i] == target { return i }
    }
    nil
}

if let i = find([4, 5, 6], 5) {
    print("found at ${i}")
}
let position = find([4, 5, 6], 9) ?? -1

let kind = match count {                      # if and match are expressions
    0 => "none"
    n if n < 10 => "a few"
    _ => "many"
}

fn counter() -> fn() -> Int {                 # closures keep their variables
    var n = 0
    fn() {
        n += 1
        n
    }
}
```

The full guide is [docs/language.md](docs/language.md).

## How it works

```text
source ──lexer──▶ tokens ──parser──▶ syntax tree ──checker──▶ typed tree ──compiler──▶ bytecode ──VM──▶ result
```

| Stage | File | Notes |
| --- | --- | --- |
| Lexer | [`lexer.py`](src/ember/lexer.py) | Go-style newlines that end statements, `${...}` interpolation lexed into nested tokens |
| Parser | [`parser.py`](src/ember/parser.py) | Recursive descent, Pratt parsing for expressions, error recovery |
| Types | [`types.py`](src/ember/types.py) | Type variables and unification, rigid generic parameters |
| Checker | [`checker.py`](src/ember/checker.py) | Inference, optionals, return paths, exhaustiveness (a matrix algorithm) |
| Compiler | [`compiler.py`](src/ember/compiler.py) | Slots for locals, upvalues for captures, typed opcodes |
| VM | [`vm.py`](src/ember/vm.py) | Stack machine with call frames, re-entrant calls from built-ins, stack traces |
| Built-ins | [`builtins.py`](src/ember/builtins.py) | Each built-in's type and implementation side by side |
| Modules | [`modules.py`](src/ember/modules.py) | Path resolution, one compile per file, `pub` visibility, cycle errors |
| REPL | [`repl.py`](src/ember/repl.py) | Persistent scope; a failed input changes nothing |

About 5,700 lines of Python. [docs/internals.md](docs/internals.md) explains the interesting parts: how inference and exhaustiveness work, and why locals live in slots instead of on the stack.

## Speed

ember runs on a bytecode VM written in Python, so it's roughly 50 to 100 times slower than Python itself. On a laptop, `fib(25)` takes about 0.7 s and a million-iteration loop about 2 s. It's fast enough for scripts, puzzles and learning how a language works, not for heavy computation. Making it faster is on the roadmap.

## Development

```console
$ python -m pip install -e . pytest
$ python -m pytest
```

There are about 210 tests, covering each stage plus every program in [`examples/`](examples), whose output must match its `.out` file exactly. CI runs them on Linux, macOS and Windows. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT
