# Changelog

## Unreleased (0.1.0)

The first version, so everything is new:

- Lexer with Go-style newlines and string interpolation; parser with error recovery.
- Static types: Int, Float, Bool, Str, Nil, lists, maps, functions, optionals, structs, enums and generic functions.
- Inference for local variables, lambda parameters and empty collections.
- Exhaustive `match` with nested patterns and guards; unreachable arms are reported.
- Closures with per-iteration loop variables.
- Bytecode compiler with typed arithmetic, and a stack VM with stack traces.
- Modules with `pub` visibility and a standard library (`math`, `text`) written in ember.
- `ember run`, `check`, `dis`, `eval` and a REPL.
- Nine example programs, run in CI against their expected output.

### Added since

- Type aliases: `type Grid = List[List[Bool]]`.
- `same(a, b)`: is it the same struct, list or map (not just an equal one)?
- `Int` methods for overflow on purpose: `wrapping_*`, `saturating_*` and `checked_* -> Int?`.

### Fixed before release

- Printing or comparing a struct that contains itself crashed; cycles now print as `...` and compare correctly.
- Recursion through callbacks (`map`, `filter`, `sort_by`...) stopped at about 150 levels; it now goes as deep as ember's own limit of 2,000 calls.
- Ctrl+C didn't stop a running program cleanly; now it does, and in the REPL the session carries on.
- `xs.push(nil)` on an empty list made it a list of Nil; it's now a list of optionals.
- A lambda couldn't satisfy an optional return type from its context (`fn() { nil }` as `fn() -> Int?`).
- `1e400` silently became `inf`; it's now a compile error.
- The sum of an empty `List[Float]` was the Int `0`.
- `-0.0` printed as `0.0`.
