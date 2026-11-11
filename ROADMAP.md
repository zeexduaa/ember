# Roadmap

ember works end to end: types, inference, generics, enums, exhaustive matching, closures, modules, a REPL and a VM. This is what's left, roughly in order. Sizes are rough: **S** is an afternoon, **M** a few days, **L** a week or more, **XL** a project of its own.

Each item is (or will be) a GitHub issue. Please comment there before starting a big one.

## v0.2: the type system

- [ ] **Tuples**: `(Int, Str)` types, `(1, "a")` values, destructuring in `let` and patterns, and `for (i, x) in xs.enumerate()`. (L)
- [ ] **Generic structs and enums**: `struct Pair[A, B] { first: A, second: B }`, `enum Tree[T] { Leaf, Node(Tree[T], T, Tree[T]) }`, with inference at construction. (XL)
- [ ] **Traits / interfaces**: `trait Show { fn show(self) -> Str }`, `impl Show for Point`, and bounds on type parameters (`fn max_of[T: Ord](xs: List[T])`), so generic code can use `<`, `+` or methods. (XL)
- [ ] **Flow-sensitive nil checks**: after `if x != nil { ... }` or an early `return` on nil, treat `x` as non-optional, as well as `if let`. (M)
- [x] **Type aliases**: `type Grid = List[List[Bool]]`. (S)
- [ ] **Range patterns and or-patterns**: `1..=9 => ...`, `"y" | "yes" => ...`, with exhaustiveness for Int ranges. (M)
- [ ] **Struct patterns**: `Point { x: 0.0, y } => ...` in match. (M)
- [ ] **Exhaustiveness for guards that are always true**: today a guarded arm never counts as covering. (S)

## v0.3: errors and the runtime

- [ ] **Recoverable errors**: a `Result[T, E]`-style built-in enum with a `?` operator to pass failures up, or `try`/`catch` for runtime errors. Needs generic enums first. (L)
- [ ] **Identity comparison**: `same(a, b)` for structs, lists and maps, alongside content `==`. (S)
- [ ] **Better runtime error locations**: point at the column (not just the line) and show the source line, as compile errors do. (M)
- [x] **Wrapping and checked arithmetic**: overflow is always an error today; add `a.wrapping_add(b)`, `a.checked_mul(b) -> Int?` and friends for code that wants something else. (S)
- [ ] **Deep recursion in built-in callbacks**: a callback that recurses very deeply through `map` can hit Python's recursion limit before ember's own. (M)

## v0.4: speed

- [ ] **Faster calls**: frames are the main cost of `fib(25)` (about 0.7 s). Try pooling frames, avoiding list slicing for arguments, and a specialized `CALL_GLOBAL`. (M)
- [ ] **Superinstructions**: fuse common pairs such as `GET_LOCAL`+`GET_LOCAL`+`IADD`, and compare-and-jump (`LT`+`JUMP_IF_FALSE`). (M)
- [ ] **Constant folding and dead code**: fold `2 * 3`, drop code after `return`, and skip `if false`. (M)
- [ ] **A benchmark suite**: fixed programs with timings tracked in CI, so speed changes are visible. (S)
- [ ] **Bytecode caching**: save compiled modules next to their source (like `.pyc`) and reuse them when unchanged. (M)

## v0.5: tools

- [ ] **Formatter**: `ember fmt` that keeps comments. The lexer drops them today, so it needs comment tokens first. (L)
- [ ] **Language server**: diagnostics, hover types, go to definition and completion over LSP, reusing the checker. (XL)
- [ ] **Debugger**: `ember debug` with breakpoints by line, step and inspecting locals, using the line table. (L)
- [ ] **Test runner**: `ember test` running `test_*` functions in `*_test.em` files, with `assert_eq` showing both sides. (M)
- [ ] **Documentation comments**: `##` comments on declarations, and `ember doc` to print a module's public API. (M)
- [ ] **REPL improvements**: re-check earlier definitions when a name is redefined with a new type, tab completion, and multi-line editing. (M)

## v0.6: the standard library

- [ ] **More of it**: `json` (parse and print), `files` (read and write text, with errors as values), `time`, `random` (seeded), `os.args`. Some need new built-ins. (L)
- [ ] **String formatting**: `"{:>8}"`-style padding and number formatting (`format(3.14159, 2)`). (M)
- [ ] **Sets**: `Set[T]` with `add`, `has`, `union`, `intersection`. (M)
- [ ] **Iterators**: lazy `map`/`filter` chains and `enumerate`/`zip` without building lists (needs tuples). (L)

## v1.0

- [ ] **A language specification**: grammar, typing rules and evaluation order, separate from the guide. (L)
- [ ] **Publish on PyPI** as `ember-lang`. (S)
- [ ] **An editor extension**: syntax highlighting for VS Code, then wiring in the language server. (M)
- [ ] **Playground**: run ember in the browser with Pyodide. (L)
