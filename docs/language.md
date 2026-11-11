# The ember language

A guide to everything ember supports today. Things that aren't supported yet are marked *(not yet)* and are on the [roadmap](../ROADMAP.md).

- [Basics](#basics)
- [Types](#types)
- [Variables](#variables)
- [Operators](#operators)
- [Control flow](#control-flow)
- [Functions and closures](#functions-and-closures)
- [Lists, maps and strings](#lists-maps-and-strings)
- [Structs and methods](#structs-and-methods)
- [Enums and match](#enums-and-match)
- [Optional values](#optional-values)
- [Generic functions](#generic-functions)
- [Modules](#modules)
- [Built-in functions](#built-in-functions)
- [Runtime errors](#runtime-errors)
- [Things to know](#things-to-know)

## Basics

```rust
# A comment runs to the end of the line.
print("Hello, world!")
let answer = 6 * 7
print("The answer is ${answer}.")     # ${...} puts any expression in a string
```

A line break ends a statement when the line could be complete. A line ending in an operator, or inside `(...)` or `[...]`, continues on the next line, and so does a line followed by one starting with `.`:

```rust
let total = 1 +
    2 +
    3
let doubled_total = [1, 2, 3]
    .map(fn(x) { x * 2 })
    .sum()
```

`;` also separates statements: `let a = 1; let b = 2`.

Names are letters, digits and `_`. By convention, values and functions are `snake_case`, and types and enum variants are `CapitalCase`. The capital letter matters in patterns (see [match](#enums-and-match)).

## Types

| Type | Values | Notes |
| --- | --- | --- |
| `Int` | `42`, `-7`, `1_000_000`, `0xff`, `0b1010`, `0o17` | 64-bit; overflow is a runtime error |
| `Float` | `3.14`, `1e9`, `2.5e-3` | 64-bit IEEE |
| `Bool` | `true`, `false` | |
| `Str` | `"text"`, `"tab\there"`, `"emoji \u{1F600}"` | Unicode; escapes `\n \t \r \0 \\ \" \$ \u{...}` |
| `Nil` | `nil` | "nothing"; what a function without a result returns |
| `List[T]` | `[1, 2, 3]` | growable, all elements the same type |
| `Map[K, V]` | `{"a": 1, "b": 2}` | keys are `Int`, `Str` or `Bool`; insertion order is kept |
| `fn(A, B) -> R` | `fn(x: Int) -> Int { x + 1 }` | functions are values |
| `T?` | `nil`, or a `T` | an optional value: see [Optional values](#optional-values) |
| `Range` | `0..10`, `1..=10` | for loops; `..` excludes the end, `..=` includes it |
| your structs and enums | | |

A **type alias** gives a type a shorter name. It's the same type, not a new one:

```rust
type Grid = List[List[Bool]]
type Handler = fn(Str) -> Int?

fn blank(w: Int, h: Int) -> Grid { ... }
```

Aliases are declared at the top level, can be `pub`, and can refer to types declared later. *(not yet: generic aliases like `type Pairs[T] = ...`)*

There are no automatic conversions. `1 + 2.0` is an error; write `float(1) + 2.0`. A `Str` is never a number either: use `str(n)` or a template string, `"${n}"`.

## Variables

```rust
let pi = 3.14159          # can't be reassigned
var count = 0             # can be
count += 1                # also -=, *=, /=, %=
let names: List[Str] = [] # a type annotation, needed when it can't be inferred
```

A variable's type is fixed when it's declared. An empty `[]` or `{}` gets its element type from later use:

```rust
var seen = []             # List[?] for now...
seen.push("a")            # ...now List[Str]
```

If nothing ever settles it, the checker asks for an annotation. Names are block-scoped, and an inner block may reuse an outer name. Declaring the same name twice in one block is an error.

Top-level `let`/`var` are globals; functions can use them wherever they're declared, but using one before its line has run is a runtime error.

## Operators

From loosest to tightest binding:

| Operators | Works on |
| --- | --- |
| `??` | `T? ?? T` gives a `T` (the default is only evaluated if needed) |
| `or` | `Bool` (short-circuits) |
| `and` | `Bool` (short-circuits) |
| `not` | `Bool` |
| `==` `!=` | any two values of the same type (compares contents), or a `T?` with `nil` |
| `<` `<=` `>` `>=` | two `Int`s, two `Float`s or two `Str`s (alphabetical) |
| `..` `..=` | two `Int`s, giving a `Range` |
| `+` `-` | numbers; `+` also joins two `Str`s or two `List`s |
| `*` `/` `%` | numbers |
| unary `-` | numbers |

`Int` division rounds toward zero (`-7 / 2` is `-3`), and `%` has the sign of the left side (`-7 % 3` is `-1`), as in C, Go and Rust. Dividing by zero is a runtime error for `Int` and `Float` alike.

Comparisons and ranges don't chain: `a < b < c` is an error, so write `(a < b) and (b < c)`.

## Control flow

`if` and `match` are expressions. A block's value is its last expression:

```rust
let size = if n > 100 { "big" } else if n > 10 { "medium" } else { "small" }

if ready {
    start()
} else {
    wait()
}
```

An `if` without `else`, used as a value, gives `nil` when the condition is false, so its type is optional (`Str?`).

```rust
while queue.len() > 0 {
    let item = queue.pop() ?? 0
    if item < 0 { continue }
    if item > 99 { break }
    process(item)
}

for i in 0..10 { }            # 0 to 9
for i in 1..=10 { }           # 1 to 10
for name in names { }         # a list's elements
for ch in "héllo" { }         # a string's characters, as one-character Strs
for key in scores { }         # a map's keys, in insertion order
```

`for` loops go over a snapshot, so changing a list inside its own loop is safe and doesn't change the loop.

## Functions and closures

```rust
fn add(a: Int, b: Int) -> Int {
    a + b                         # the last expression is the result
}

fn greet(name: Str) {             # no "-> type": returns nil
    print("hi ${name}")
}

fn sign(n: Int) -> Int {
    if n < 0 { return -1 }        # return works anywhere
    if n == 0 { return 0 }
    1
}
```

The checker makes sure a function returns its declared type on every path. Parameter types are always written out. Top-level functions can be used before they're declared, and can call each other.

Functions are values. A function written inline (a lambda) can leave out types the context already gives:

```rust
let double = fn(x: Int) -> Int { x * 2 }
let squares = [1, 2, 3].map(fn(x) { x * x })        # x: Int and the result are inferred
let by_length = words.sort_by(fn(w) { w.len() })
```

Closures capture variables, not copies, and can change them:

```rust
fn counter() -> fn() -> Int {
    var n = 0
    fn() {
        n += 1
        n
    }
}
let next = counter()
next()        # 1
next()        # 2
```

Each loop iteration has its own loop variable, so closures created in a loop remember their own value.

A function can be declared inside another one, and can call itself.

## Lists, maps and strings

```rust
var xs = [3, 1, 2]
xs.push(4)
xs[0] = 10            # out of range is a runtime error; xs[-1] is the last element
let ys = xs + [5]     # a new list
```

**List methods:** `len()`, `is_empty()`, `push(x)`, `pop() -> T?`, `insert(i, x)`, `remove(i) -> T`, `extend(other)`, `clear()`, `contains(x)`, `index_of(x) -> Int?`, `first() -> T?`, `last() -> T?`, `slice(start, end)`, `copy()`, `reversed()`, `sorted()`, `sort()`, `sum()`, `min() -> T?`, `max() -> T?`, `join(sep)` (lists of Str), `map(f)`, `filter(f)`, `find(f) -> T?`, `any(f)`, `all(f)`, `count(f)`, `each(f)`, `reduce(init, f)`, `sort_by(key)`.

`sorted`, `sort`, `min`, `max` and `sort_by`'s key need `Int`, `Float` or `Str`; `sum` needs numbers. `sort_by` is stable.

```rust
let ages = {"ada": 36, "alan": 41}
ages["grace"] = 85
let a = ages["ada"]              # a missing key is a runtime error...
let b = ages.get("bob") ?? 0     # ...so use get() when it might be missing
```

**Map methods:** `len()`, `is_empty()`, `get(k) -> V?`, `get_or(k, default)`, `set(k, v)`, `has(k)`, `remove(k) -> V?`, `keys()`, `values()`, `copy()`, `clear()`.

**String methods:** `len()` (in characters), `is_empty()`, `upper()`, `lower()`, `trim()`, `split(sep)`, `lines()`, `chars()`, `contains(s)`, `starts_with(s)`, `ends_with(s)`, `index_of(s) -> Int?`, `replace(old, new)`, `repeat(n)`, `slice(start, end)`. `s[i]` is the character at `i`. Strings can't be changed in place; build new ones.

**Number methods:** `Int`: `abs()`, `pow(n)`, `to_float()`. `Float`: `abs()`, `floor()`, `ceil()`, `round()` (to `Int`), `is_nan()`.

## Structs and methods

```rust
struct Account { owner: Str, balance: Int }

impl Account {
    fn deposit(self, amount: Int) { self.balance += amount }   # self: a method
    fn open(owner: Str) -> Account { Account { owner, balance: 0 } }   # no self: static
}

let acct = Account.open("Ada")       # `owner` alone is short for `owner: owner`
acct.deposit(50)
print(acct)                          # Account { owner: "Ada", balance: 50 }
```

Every field must be given in a struct literal. Fields are always changeable, even through a `let` (which only stops the variable from pointing at another struct).

Structs are shared, not copied: after `let b = acct`, `b` and `acct` are the same account. `==` compares contents, so two separate structs with equal fields are `==`.

Inside an `if`, `while`, `for` or `match` head, a struct literal needs parentheses: `if p == (Point { x: 0.0, y: 0.0 }) { ... }`. Otherwise the `{` would start the block, as in Rust.

## Enums and match

```rust
enum Shape {
    Circle(Float)             # a variant with a field
    Rect(Float, Float)
    Empty                     # one without
}

let s = Shape.Rect(2.0, 3.0)
let e = Shape.Empty
```

`match` picks the first arm whose pattern fits:

```rust
fn describe(s: Shape) -> Str {
    match s {
        Circle(r) if r > 10.0 => "a big circle"     # a guard
        Circle(r) => "a circle of radius ${r}"
        Rect(w, h) => "a ${w} by ${h} rectangle"
        Empty => "nothing"
    }
}
```

Patterns:

| Pattern | Matches |
| --- | --- |
| `_` | anything |
| `name` (lowercase) | anything, and names it |
| `42`, `"text"`, `true`, `nil`, `-1` | that value |
| `Circle(r)`, `Shape.Circle(r)` | that variant; the parts are patterns too, so `Add(Num(0), b)` works |
| `Empty` | a variant without fields |

The checker requires a `match` to handle every possible value, and says which one is missing. Arms with a guard don't count toward that, because the guard might be false. An arm after a catch-all is reported as unreachable.

An arm's result is an expression, or a block in `{ }` whose last expression is the result. A `match` used as a statement doesn't need its arms to agree on a type.

Enums can have methods in an `impl` block, like structs. A variant with fields is also a function: `[1.0, 2.0].map(Shape.Circle)`.

## Optional values

`T?` is a `T` or `nil`. You can't use it as a `T` until you've checked:

```rust
let m = {"a": 1}
let found = m.get("a")             # Int?
found + 1                          # error: one side might be nil

if let n = found { print(n + 1) }  # n is an Int here
let n = found ?? 0                 # a default
let text = match found {
    nil => "missing"
    n => "got ${n}"                # after a nil arm, n is an Int
}
```

`nil` fits anywhere a `T?` is expected, and so does a plain `T`. An optional can't be optional again (`Int??`).

## Generic functions

```rust
fn first[T](xs: List[T]) -> T? {
    if xs.is_empty() { return nil }
    xs[0]
}

fn apply[T, U](xs: List[T], f: fn(T) -> U) -> List[U] {
    var out: List[U] = []
    for x in xs { out.push(f(x)) }
    out
}

first([1, 2])                       # Int?
apply(["a", "bb"], fn(s) { s.len() })   # List[Int]
```

Type parameters are inferred at each call. Inside the function, a `T` can only be passed around and compared with `==`: there are no constraints like "T supports +" *(not yet: traits)*, and structs and enums can't be generic *(not yet)*.

## Modules

```rust
# geometry.em
pub struct Vec2 { x: Float, y: Float }
pub fn length(v: Vec2) -> Float { sqrt(v.x * v.x + v.y * v.y) }
fn helper() -> Int { 1 }            # private: not visible to importers
```

```rust
# main.em
import "geometry"                   # geometry.em next to this file
import "lib/shapes" as shapes       # a path, relative to this file, and a chosen name
import "math"                       # the standard library

let v = geometry.Vec2 { x: 3.0, y: 4.0 }
print(geometry.length(v))
```

Only `pub` declarations are visible. A struct or enum's methods come with it. Imported types work in annotations, literals, static calls and patterns: `geometry.Vec2`, `geometry.Shape.Circle(1.0)`.

A module runs once, the first time it's imported, and keeps its own globals. An import cycle (a imports b, and b imports a) is an error.

**Standard library** (written in ember, in [`src/ember/stdlib`](../src/ember/stdlib)):

- `math`: `PI`, `E`, `gcd`, `lcm`, `clamp`, `clamp_int`, `is_prime`, `primes_below`, `factorial`, `mean`
- `text`: `pad_left`, `pad_right`, `capitalize`, `words`, `reverse`, `is_palindrome`, `word_counts`

A file of yours with the same name takes precedence over the standard library's.

## Built-in functions

| Function | |
| --- | --- |
| `print(a, b, ...)` | writes the values separated by spaces, then a line break |
| `str(x) -> Str` | how `print` shows x |
| `input(prompt) -> Str?` | a line typed by the user; nil at the end of input |
| `int(x: Float) -> Int` | drops the fraction |
| `float(x: Int) -> Float` | |
| `parse_int(s) -> Int?`, `parse_float(s) -> Float?` | nil if s isn't a number |
| `round(x)`, `floor(x)`, `ceil(x)` | `Float -> Int`; halves round up (`round(2.5)` is 3) |
| `sqrt(x)`, `pow(a, b)` | `Float` |
| `min(a, b)`, `max(a, b)` | two Ints, Floats or Strs |
| `assert(cond, message?)` | stops the program if cond is false |
| `panic(message)` | stops the program; fits wherever any value is expected |
| `clock() -> Float` | seconds, for timing |

## Runtime errors

Some mistakes can only be caught while running: an index out of range, a missing map key, dividing by zero, an `Int` overflowing 64 bits, recursion more than 2,000 calls deep, `assert` and `panic`. The program stops with a message and an ember stack trace:

```text
runtime error: index 5 is out of range for a list of length 2
  at get (main.em:1)
  at outer (main.em:2)
  at <main> (main.em:3)
```

There's no way to catch them yet *(not yet)*. Return an enum or an optional for failures the caller should handle, as [`examples/bank.em`](../examples/bank.em) does.

## Things to know

- Structs, lists and maps are shared references; numbers, strings and enum values behave like values.
- `==` compares contents (lists, maps and structs included). There's no identity comparison *(not yet)*.
- String length and indexing count characters (code points), not bytes.
- A method call binds tighter than a minus sign, as in Rust: `-5.abs()` is `-(5.abs())`, which is -5. Write `(-5).abs()`.
- Warnings don't stop a program: unused variables (name them `_x` to silence it), unused values, unreachable match arms, `??` on something that's never nil, and exact `Float` patterns.
- In the REPL, redefining a name only affects code typed afterwards.
