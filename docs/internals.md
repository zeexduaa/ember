# How ember works

A tour of the implementation for anyone who wants to change it, or who is curious how a typed language is put together. Each section names the file to read next.

## The pipeline

```text
text ─▶ Lexer ─▶ tokens ─▶ Parser ─▶ AST ─▶ Checker ─▶ typed AST ─▶ Compiler ─▶ FunctionProto ─▶ VM
```

[`driver.py`](../src/ember/driver.py) wires the stages together. Every stage reports problems as `Diagnostic`s ([`source.py`](../src/ember/source.py)), which carry a `Span` of byte offsets. Rendering turns the span into `file:line:col`, the source line and a caret. A stage collects all its diagnostics, then raises one `EmberError`.

## Lexing: newlines and templates

[`lexer.py`](../src/ember/lexer.py)

**Newlines.** ember has no semicolons, but `1 +⏎ 2` is one statement and `f()⏎ g()` is two. The rule, borrowed from Go: a newline becomes a `newline` token only if the last token on the line could end a statement (a name, a literal, `)`, `]`, `}`, `return`...). It never does inside `( )` or `[ ]`, or when the next line starts with `.`, so method chains can be split across lines. Braces don't suppress newlines, because they enclose statements.

**Templates.** `"a ${x + 1} b"` becomes one `template` token whose value is `["a ", [tokens of x + 1], " b"]`. When the lexer meets `${`, it lexes recursively until the matching `}`, counting braces so `${ {"k": 1}["k"] }` works. The parser parses each token list with a sub-parser.

## Parsing

[`parser.py`](../src/ember/parser.py)

Statements use recursive descent. Expressions use precedence climbing: `expression(min_prec)` parses a prefix, then keeps folding binary operators whose precedence is at least `min_prec`, recursing with `prec + 1` (left-associative) or `prec` (`??` is right-associative). Comparison and range operators are non-associative, so a second one at the same level is an error.

Two things are worth knowing:

- **`no_struct`.** In `if x == Origin { ... }`, `Origin {` could start a struct literal. While parsing the head of `if`, `while`, `for` or `match`, struct literals are switched off, as in Rust. Parentheses switch them back on.
- **Recovery.** When a statement fails to parse, the error is recorded and the parser skips to the end of that statement (a newline or `;` outside brackets, or the `}` closing the block), then carries on. One run reports up to 25 errors.

## Types and inference

[`types.py`](../src/ember/types.py), [`checker.py`](../src/ember/checker.py)

Types are small immutable values: `Prim("Int")`, `ListT(elem)`, `MapT(k, v)`, `FnT(params, ret)`, `OptionT(inner)`. Structs and enums are compared by identity.

**Unification.** An unknown type is a `TypeVar` with a `ref` that unification fills in. `unify(a, b)` follows the refs (`prune`), binds a variable to the other side after an occurs check, and recurses into lists, maps, functions and optionals. This is how `var xs = []` becomes `List[Str]` when `xs.push("a")` is checked later. The checker keeps a list of variables created for empty literals and reports any still unbound at the end.

**Fitting, not just equality.** `fits(actual, expected)` is unification plus one rule: a `T` or `nil` fits a `T?`. `NEVER` (the type of `panic(...)`) fits anything.

**Checking is bidirectional.** `check_expr(expr, expected)` passes down the type the context wants. That's what lets `[1, 2].map(fn(x) { x * 2 })` work: `map`'s signature for a `List[Int]` is `fn(fn(Int) -> U) -> List[U]` with a fresh `U`. The lambda is checked with that expected type, so `x` gets `Int`, and its body's type is unified into `U`.

**Generics.** A generic function's type parameters are `TypeParam`s. Inside the body they're rigid: only equal to themselves, so `x + 1` on a `T` is an error. At each use, `instantiate` substitutes fresh `TypeVar`s, and the call's arguments decide them.

**Return paths.** `always_exits(stmt)` is true for `return`, a call that returns `Never`, an `if`/`else` whose branches both exit, and a `match` whose arms all do. A function with a non-`Nil` result must end with an expression of the right type or always exit.

**Resolution for the compiler.** While checking, the checker records how things resolved: `Name.binding`, `Field.kind` (`field`, `variant`, `static`, `module`), `Call.kind` (`function`, `method`, `builtin`, `builtin_method`, `variant`, `static`), and struct field indexes. So the compiler never has to work out types again.

## Exhaustiveness

`missing_cases(types, rows)` in [`checker.py`](../src/ember/checker.py) is a small version of the pattern-matrix algorithm used by OCaml and Rust (Maranget, "Warnings for pattern matching"). Each unguarded arm is a row of patterns, one column per value still to examine:

1. No columns left: covered if any row is left.
2. No rows: not covered, and `_` is an example.
3. A row of catch-alls covers everything.
4. If every row has a catch-all in the first column, drop the column. This step matters for recursive enums: expanding a column nobody looks into would never end.
5. Otherwise split on the first column's type:
   - **Enum:** for each variant, keep the rows that match it, replacing the pattern by its sub-patterns (a catch-all expands to one catch-all per field), and recurse. If a variant isn't covered, build the example: `Shape.Rect(_, _)`.
   - **Bool:** the same, with `true` and `false`.
   - **Optional:** `nil`, then the inner type.
   - **Int/Str/Float:** infinitely many values, so only catch-alls count. The example is `another Int` if some literals were tried, else `_`.

## Bytecode

[`bytecode.py`](../src/ember/bytecode.py), [`compiler.py`](../src/ember/compiler.py)

A `FunctionProto` has a flat `code` list (an opcode, then its operands), a parallel `lines` list for error messages, `constants`, `upvalues` descriptors and `max_locals`. `ember dis file.em` prints it.

**Locals live in slots, not on the stack.** Each frame has a fixed array of local slots plus a separate operand stack. A local is read with `GET_LOCAL slot` and written with `SET_LOCAL slot`. Two things fall out of this:

- **Pattern matching is simple.** A `match` stores the subject in a hidden slot. Each pattern test loads from slots, compares, and `JUMP_IF_FALSE`s to the next arm. A failed test never leaves half-built values on the stack, because bindings are just names for slots (`BindPat` adds an alias, no copy).
- **Leaving a scope costs nothing.** No pops are needed when a block ends. The slots are just reused by the next declaration.

**Typed opcodes.** The checker knows every operand's type, so `a + b` compiles to `IADD`, `FADD`, `SADD` or `LADD`. The VM never inspects types for arithmetic. Only `IADD`/`ISUB`/`IMUL`/`INEG` check for 64-bit overflow.

**Closures and upvalues.** This works as in Lua and clox. When a lambda uses a variable of an enclosing function, the compiler marks that local `captured` and gives the lambda an upvalue descriptor: `(is_local, index)` means "slot `index` of my parent" or "upvalue `index` of my parent". At run time, `CLOSURE` creates `Upvalue` objects that point into the frame's slot array while it's alive, sharing one per slot so all closures see the same variable. When the variable's scope ends, `CLOSE_UPVALUE slot` copies the value into the upvalue and unhooks it. That runs at the end of every loop iteration, so each iteration's closures get their own variable.

**Control flow.** Jumps use absolute targets, back-patched once known. `break` and `continue` first close any captured locals in the scopes they leave. A `for` loop keeps its iterator on the stack. `FOR_ITER` either pushes the next item or jumps to the loop's end, where the iterator is popped (`break` jumps there too).

## The VM

[`vm.py`](../src/ember/vm.py)

`_loop` is one big `while True` with the hot state (code, constants, locals, stack, ip) in Python local variables. The most frequent instructions come first in the `if`/`elif` chain, and rarer ones go to `_slow`. In CPython this layout matters more than anything else.

**Calls.** `CALL n` pops the arguments into a new frame's first slots and switches the loop's local variables to the new frame. `RETURN` closes the frame's open upvalues, pops it, and pushes the result onto the caller's stack. There's no Python recursion for ember calls, so the depth limit (2,000) is ember's own.

**Re-entrant calls.** Built-ins such as `map`, `filter` and `sort_by` call ember functions through `call_value`. It pushes a frame and runs a nested `_run(base)` that returns when that frame returns. Errors raised there carry the whole ember stack.

**Modules.** Each module's functions run with that module's globals dict, chosen by `FunctionProto.module` on every call. That's how a callback passed from `main.em` into another module still sees `main.em`'s variables. `IMPORT` runs a module's top level the first time and caches the resulting `Module`.

**Errors.** Runtime problems raise `EmberPanic`. The loop sets `frame.ip` before anything that can fail, so `_trace()` can map each frame's ip to a line. `RuntimeFailure.render` collapses repeated frames (deep recursion) and cuts long traces.

## Modules

[`modules.py`](../src/ember/modules.py)

The checker gets an `import_module(stmt)` callback. The loader resolves the path relative to the importing file, falling back to `stdlib/`. It checks and compiles the file with its own `Checker`, under a module id (its absolute path), and caches it. It returns a `ModuleT` whose `members` are the `pub` bindings. A list of files being loaded detects cycles. If an import fails, a "broken" placeholder module absorbs later uses, so one bad import doesn't produce dozens of errors.

## The REPL

[`repl.py`](../src/ember/repl.py)

There's one `Checker` (in REPL mode, which allows redefining globals) and one VM globals dict for the whole session. Each input is parsed, checked against the shared scope, compiled with `return_last=True` (a final expression's value is returned instead of popped), and run against the shared globals. If checking fails, the scope is restored from a snapshot, so a bad input defines nothing. An input continues over several lines while parsing fails with "end of file".

## Adding a built-in method

1. In [`builtins.py`](../src/ember/builtins.py), add the implementation to `IMPLS[kind]`: `lambda vm, receiver, *args: ...`. Raise `EmberPanic` for runtime errors.
2. Add the signature to the matching `_..._methods` table, using the receiver's element types (`T`, `K`, `V`) and fresh `TypeVar`s for generic results.
3. Add a test in `tests/test_vm.py`, and a line in [docs/language.md](language.md).
