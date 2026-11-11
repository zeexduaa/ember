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
