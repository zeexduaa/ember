"""Static type checking.

The checker walks the syntax tree once, giving every expression a type (stored
in ``expr.type``) and recording how names, fields and calls resolve so the
compiler doesn't have to work it out again. It collects every error it finds
instead of stopping at the first.

What it checks, briefly:

- every name is defined, and ``let`` bindings aren't reassigned;
- operators get operands they work on (no mixing Int and Float without float());
- calls get the right number and types of arguments, including generic
  functions, whose type parameters are inferred at each call;
- optional values (``T?``) are unwrapped with ``if let``, ``??`` or ``match``
  before use;
- functions return their declared type on every path;
- ``match`` covers every case (missing enum variants are listed), and arms
  after a catch-all are reported as unreachable;
- empty ``[]`` and ``{}`` get their element types from how they're used.
"""

from __future__ import annotations

import difflib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from . import ast as A
from .builtins import BUILTIN_FUNCTIONS, builtin_method
from .source import Diagnostic, EmberError, Source, Span
from .types import (BOOL, FLOAT, INT, NEVER, NIL, PRIMS, RANGE, STR, EnumT, FnT, ListT, MapT, Method, ModuleT,
                    OptionT, Prim, StructT, Type, TypeParam, TypeVar, has_unknowns, is_comparable, is_hashable,
                    is_numeric, occurs, prune, resolve, substitute, unify)

INT_MIN, INT_MAX = -(2 ** 63), 2 ** 63 - 1


@dataclass(eq=False)
class Binding:
    name: str
    type: Type
    kind: str                  # "var", "let", "param", "function", "struct", "enum", "module", "builtin", "type_param"
    span: Span | None
    mutable: bool = False
    global_: bool = False
    used: bool = False
    type_params: list[TypeParam] = field(default_factory=list)   # for generic functions
    extra: Any = None          # StructT / EnumT / ModuleT for type-like bindings
    public: bool = False


class Scope:
    def __init__(self, parent: Scope | None, kind: str = "block"):
        self.parent = parent
        self.kind = kind
        self.names: dict[str, Binding] = {}

    def lookup(self, name: str) -> Binding | None:
        scope: Scope | None = self
        while scope is not None:
            if name in scope.names:
                return scope.names[name]
            scope = scope.parent
        return None

    def all_names(self) -> list[str]:
        names: list[str] = []
        scope: Scope | None = self
        while scope is not None:
            names.extend(scope.names)
            scope = scope.parent
        return names


@dataclass
class FnContext:
    ret: Type
    name: str
    loop_depth: int = 0


class CheckError(Exception):
    """Internal: stops checking one expression after an error has been recorded."""


class Checker:
    def __init__(self, source: Source, module_name: str = "main",
                 import_module: Callable[[A.Import], ModuleT] | None = None, repl: bool = False):
        self.source = source
        self.module_name = module_name
        self.import_module = import_module
        self.errors: list[Diagnostic] = []
        self.warnings: list[Diagnostic] = []
        self.globals = Scope(None, "module")
        self.scope = self.globals
        self.fn: FnContext | None = None
        self.pending_vars: list[tuple[TypeVar, Span, str]] = []   # empty literals waiting for a type
        self.repl = repl          # the REPL may redefine top-level names
        self.imported_errors: list[EmberError] = []
        self._owner_ids = iter(range(1, 10 ** 9))
        for name, spec in BUILTIN_FUNCTIONS.items():
            self.globals.names[name] = Binding(name, spec.type or FnT((), NIL), "builtin", None, global_=True, extra=spec)

    # -- reporting ----------------------------------------------------------------------

    def error(self, message: str, span: Span | None, notes: list[str] | None = None) -> None:
        self.errors.append(Diagnostic(message, span, notes=notes or []))

    def fail(self, message: str, span: Span | None, notes: list[str] | None = None) -> CheckError:
        self.error(message, span, notes)
        return CheckError()

    def warn(self, message: str, span: Span | None, notes: list[str] | None = None) -> None:
        self.warnings.append(Diagnostic(message, span, kind="warning", notes=notes or []))

    def expect(self, actual: Type, expected: Type, span: Span, what: str = "") -> None:
        """Reports an error unless a value of type ``actual`` fits where ``expected`` is needed.
        Nil and T both fit T?."""
        if not self.fits(actual, expected):
            exp, act = resolve(expected), resolve(actual)
            pe, pa = prune(expected), prune(actual)
            if (isinstance(pe, TypeVar) and occurs(pe, pa)) or (isinstance(pa, TypeVar) and occurs(pa, pe)):
                self.error("a value can't contain itself (its type would never end)", span,
                           notes=["e.g. a list pushed into itself; use a struct to build recursive data"])
                return
            notes = []
            if prune(exp) == FLOAT and prune(act) == INT:
                notes.append("convert with float(...), or write the number with a decimal point (1.0)")
            elif prune(exp) == INT and prune(act) == FLOAT:
                notes.append("convert with int(...) (rounds toward zero) or round(...)")
            elif isinstance(prune(act), OptionT) and self.fits(prune(act).inner, exp):
                notes.append("the value might be nil; unwrap it with `if let`, `??` or `match` first")
            elif prune(exp) == STR and prune(act) in (INT, FLOAT, BOOL):
                notes.append("convert with str(...), or use a template string: \"${...}\"")
            prefix = f"{what}: " if what else ""
            self.error(f"{prefix}expected {exp}, found {act}", span, notes)

    def fits(self, actual: Type, expected: Type) -> bool:
        actual, expected = prune(actual), prune(expected)
        if actual == NEVER:
            return True
        if isinstance(expected, OptionT) and not isinstance(actual, (OptionT, TypeVar)):
            return actual == NIL or self.fits(actual, expected.inner)
        if actual == NIL and isinstance(expected, TypeVar):
            # nil arriving where the type isn't known yet (xs.push(nil) on an empty list): it's optional.
            return unify(expected, OptionT(TypeVar()))
        return unify(actual, expected)

    # -- entry point ------------------------------------------------------------------------

    def public_members(self) -> dict[str, Binding]:
        """What other files see after `import`: the top-level declarations marked pub."""
        return {name: b for name, b in self.globals.names.items() if b.public and b.kind != "builtin"}

    def check_program(self, program: A.Program) -> None:
        deferred: list[tuple[A.FnDecl, Scope]] = []
        self.declare_types(program.stmts)
        for stmt in program.stmts:
            if isinstance(stmt, A.FnDecl):
                self.declare_function(stmt, self.globals, global_=True)
            elif isinstance(stmt, A.ImplDecl):
                self.declare_impl(stmt)
        for stmt in program.stmts:
            try:
                if isinstance(stmt, A.FnDecl):
                    deferred.append((stmt, self.globals))
                elif isinstance(stmt, A.ImplDecl):
                    for method in stmt.methods:
                        deferred.append((method, self.globals))
                elif isinstance(stmt, (A.StructDecl, A.EnumDecl, A.Import, A.TypeAlias)):
                    continue
                else:
                    self.check_stmt(stmt)
            except CheckError:
                pass
        for decl, scope in deferred:
            try:
                self.check_function_body(decl, scope)
            except CheckError:
                pass
        for var, span, what in self.pending_vars:
            if prune(var) is var:
                self.error(f"can't tell the type of {what}", span,
                           notes=["add a type, e.g. let xs: List[Int] = [] or let m: Map[Str, Int] = {}"])

    # -- declarations ------------------------------------------------------------------------

    def declare_types(self, stmts: list[A.Stmt]) -> None:
        """Structs and enums first (they can refer to each other), then imports."""
        for stmt in stmts:
            if isinstance(stmt, A.Import):
                self.declare_import(stmt)
        for stmt in stmts:
            if isinstance(stmt, A.TypeAlias):
                if self.globals.names.get(stmt.name) and self.globals.names[stmt.name].kind != "builtin" \
                        and not self.repl:
                    self.error(f"'{stmt.name}' is already defined", stmt.name_span)
                    continue
                if not stmt.name[0].isupper():
                    self.error(f"type names start with a capital letter: {stmt.name[0].upper() + stmt.name[1:]}",
                               stmt.name_span)
                # Resolved on first use (aliases may refer to types and aliases declared later).
                self.globals.names[stmt.name] = Binding(stmt.name, NIL, "alias", stmt.name_span, global_=True,
                                                        extra=None, public=stmt.public, type_params=[])
                self.globals.names[stmt.name].decl = stmt  # type: ignore[attr-defined]
            elif isinstance(stmt, (A.StructDecl, A.EnumDecl)):
                if self.globals.names.get(stmt.name) and self.globals.names[stmt.name].kind != "builtin" \
                        and not self.repl:
                    self.error(f"'{stmt.name}' is already defined", stmt.name_span)
                    continue
                if not stmt.name[0].isupper():
                    self.error(f"type names start with a capital letter: {stmt.name[0].upper() + stmt.name[1:]}",
                               stmt.name_span)
                is_struct = isinstance(stmt, A.StructDecl)
                t: Type = StructT(stmt.name, self.module_name) if is_struct else EnumT(stmt.name, self.module_name)
                self.globals.names[stmt.name] = Binding(stmt.name, t, "struct" if is_struct else "enum", stmt.name_span,
                                                        global_=True, extra=t, public=stmt.public)
        for stmt in stmts:
            try:
                if isinstance(stmt, A.TypeAlias) and self.globals.names.get(stmt.name) is not None:
                    self.resolve_alias(self.globals.names[stmt.name])
                elif isinstance(stmt, A.StructDecl):
                    t = self.globals.names[stmt.name].extra
                    if isinstance(t, StructT):
                        for f in stmt.fields:
                            t.fields[f.name] = self.resolve_type(f.type)
                elif isinstance(stmt, A.EnumDecl):
                    t = self.globals.names[stmt.name].extra
                    if isinstance(t, EnumT):
                        for v in stmt.variants:
                            t.variants.append((v.name, tuple(self.resolve_type(f) for f in v.fields)))
            except CheckError:
                pass

    def resolve_alias(self, binding: Binding) -> Type:
        if binding.extra is not None:
            return binding.extra
        decl: A.TypeAlias = binding.decl  # type: ignore[attr-defined]
        if getattr(binding, "resolving", False):
            binding.extra = TypeVar()
            raise self.fail(f"type alias '{binding.name}' refers to itself", decl.name_span,
                            notes=["use a struct or enum for recursive types"])
        binding.resolving = True  # type: ignore[attr-defined]
        try:
            binding.extra = self.resolve_type(decl.target)
        finally:
            binding.resolving = False  # type: ignore[attr-defined]
        binding.type = binding.extra
        return binding.extra

    def declare_import(self, stmt: A.Import) -> None:
        if self.import_module is None:
            self.error("imports only work when running a file (ember run file.em)", stmt.path_span)
            return
        try:
            module = self.import_module(stmt)
        except CheckError:
            self._define_broken_module(stmt)
            return
        except EmberError as exc:
            self._define_broken_module(stmt)
            if exc.source is None:
                # A problem with the import itself (not found, a cycle): report it at the import.
                for d in exc.diagnostics:
                    self.errors.append(Diagnostic(d.message, d.span or stmt.path_span, notes=d.notes))
                return
            # Errors inside the imported file are shown as they are (pointing into that file).
            self.errors.append(Diagnostic(f"can't import \"{stmt.path}\" because of the errors above",
                                          stmt.path_span))
            self.imported_errors.append(exc)
            return
        if stmt.alias in self.globals.names and self.globals.names[stmt.alias].kind != "builtin":
            self.error(f"'{stmt.alias}' is already defined; import it with another name: import \"{stmt.path}\" as other",
                       stmt.path_span)
            return
        self.globals.names[stmt.alias] = Binding(stmt.alias, module, "module", stmt.path_span, global_=True,
                                                 extra=module)

    def _define_broken_module(self, stmt: A.Import) -> None:
        """After a failed import, the name still exists, so its uses don't each add an error."""
        if stmt.alias not in self.globals.names:
            module = ModuleT(stmt.alias, stmt.path)
            module.broken = True
            self.globals.names[stmt.alias] = Binding(stmt.alias, module, "module", stmt.path_span, global_=True,
                                                     used=True, extra=module)

    def function_type(self, decl: A.FnDecl, self_type: Type | None = None) -> tuple[FnT, list[TypeParam]]:
        owner = next(self._owner_ids)
        params: list[TypeParam] = []
        scope = Scope(self.scope, "type_params")
        for name, span in decl.type_params:
            if name in PRIMS or not name[:1].isupper():
                self.error(f"type parameters are capitalized names like T, found '{name}'", span)
            tp = TypeParam(name, owner)
            params.append(tp)
            scope.names[name] = Binding(name, tp, "type_param", span, extra=tp)
        saved = self.scope
        self.scope = scope
        try:
            param_types = []
            for p in decl.params:
                try:
                    param_types.append(self.resolve_type(p.type))
                except CheckError:
                    param_types.append(TypeVar())
            try:
                ret = self.resolve_type(decl.ret) if decl.ret else NIL
            except CheckError:
                ret = TypeVar()
        finally:
            self.scope = saved
        return FnT(tuple(param_types), ret), params

    def declare_function(self, decl: A.FnDecl, scope: Scope, global_: bool = False) -> Binding:
        fn_type, type_params = self.function_type(decl)
        existing = scope.names.get(decl.name)
        if existing is not None and existing.kind != "builtin" and not (self.repl and scope is self.globals):
            self.error(f"'{decl.name}' is already defined in this scope", decl.name_span)
        binding = Binding(decl.name, fn_type, "function", decl.name_span, global_=global_,
                          type_params=type_params, public=decl.public)
        scope.names[decl.name] = binding
        decl.info = binding
        return binding

    def declare_impl(self, impl: A.ImplDecl) -> None:
        binding = self.globals.names.get(impl.type_name)
        if binding is None or binding.kind not in ("struct", "enum"):
            self.error(f"impl needs a struct or enum declared in this file, and '{impl.type_name}' isn't one",
                       impl.name_span)
            return
        target = binding.extra
        for decl in impl.methods:
            if decl.name in target.methods:
                self.error(f"{impl.type_name} already has a method '{decl.name}'", decl.name_span)
                continue
            if isinstance(target, StructT) and decl.name in target.fields:
                self.error(f"{impl.type_name} has a field named '{decl.name}'; a method can't use the same name",
                           decl.name_span)
                continue
            fn_type, type_params = self.function_type(decl)
            method = Method(decl.name, fn_type, decl.has_self, type_params, decl)
            target.methods[decl.name] = method
            decl.info = Binding(decl.name, fn_type, "function", decl.name_span, global_=True,
                                type_params=type_params, extra=(target, method))

    # -- types written in the source ------------------------------------------------------------

    def resolve_type(self, texpr: A.TypeExpr | None) -> Type:
        if texpr is None:
            raise self.fail("missing type", None)
        if isinstance(texpr, A.OptionalType):
            inner = self.resolve_type(texpr.inner)
            if isinstance(prune(inner), OptionT):
                raise self.fail("a type can only be optional once (T??)", texpr.span)
            if prune(inner) == NIL:
                raise self.fail("Nil? isn't useful; use Nil", texpr.span)
            return OptionT(inner)
        if isinstance(texpr, A.FnType):
            return FnT(tuple(self.resolve_type(p) for p in texpr.params),
                       self.resolve_type(texpr.ret) if texpr.ret else NIL)
        assert isinstance(texpr, A.NamedType)
        name, args = texpr.name, texpr.args
        if name in PRIMS and name != "Range":
            if args:
                raise self.fail(f"{name} doesn't take type arguments", texpr.span)
            return PRIMS[name]
        if name == "List":
            if len(args) != 1:
                raise self.fail("List takes one type: List[Int]", texpr.span)
            return ListT(self.resolve_type(args[0]))
        if name == "Map":
            if len(args) != 2:
                raise self.fail("Map takes two types: Map[Str, Int]", texpr.span)
            key = self.resolve_type(args[0])
            if not is_hashable(key):
                raise self.fail(f"map keys must be Int, Str or Bool, not {key}", args[0].span)
            return MapT(key, self.resolve_type(args[1]))
        if "." in name:
            module_name, _, member = name.partition(".")
            binding = self.scope.lookup(module_name)
            if binding is None or binding.kind != "module":
                raise self.fail(f"unknown module '{module_name}'", texpr.span)
            if binding.extra.broken:
                raise CheckError()
            target = binding.extra.members.get(member)
            if target is not None and target.kind == "alias":
                binding.used = True
                return target.extra
            if target is None or target.kind not in ("struct", "enum"):
                raise self.fail(f"module {module_name} has no public type '{member}'", texpr.span)
            binding.used = True
            return target.extra
        binding = self.scope.lookup(name)
        if binding is not None and binding.kind == "alias":
            if args:
                raise self.fail(f"{name} doesn't take type arguments (generic aliases aren't supported yet)",
                                texpr.span)
            binding.used = True
            return self.resolve_alias(binding)
        if binding is None or binding.kind not in ("struct", "enum", "type_param"):
            known = [n for n in self.scope.all_names()
                     if (b := self.scope.lookup(n)) and b.kind in ("struct", "enum", "type_param", "alias")]
            close = difflib.get_close_matches(name, known + ["Int", "Float", "Bool", "Str", "List", "Map"], n=1)
            raise self.fail(f"unknown type '{name}'", texpr.span, [f"did you mean {close[0]}?"] if close else [])
        if args:
            raise self.fail(f"{name} doesn't take type arguments (generic structs aren't supported yet)", texpr.span)
        binding.used = True
        return binding.extra

    # -- statements ------------------------------------------------------------------------------

    def check_block(self, block: A.Block, expected: Type | None = None, value: bool = False) -> Type:
        """Checks a block in a new scope. With ``value``, the last expression statement is the block's value."""
        saved = self.scope
        self.scope = Scope(saved)
        result: Type = NIL
        try:
            for i, stmt in enumerate(block.stmts):
                last = i == len(block.stmts) - 1
                try:
                    if last and value and isinstance(stmt, A.ExprStmt):
                        result = self.check_expr(stmt.expr, expected)
                    else:
                        self.check_stmt(stmt)
                        if last and value and always_exits(stmt):
                            result = NEVER
                except CheckError:
                    if last and value:
                        result = TypeVar()
            self.report_unused(self.scope)
        finally:
            self.scope = saved
        return result

    def report_unused(self, scope: Scope) -> None:
        for binding in scope.names.values():
            if not binding.used and binding.kind in ("let", "var") and not binding.name.startswith("_"):
                self.warn(f"'{binding.name}' is never used", binding.span,
                          notes=[f"if that's on purpose, name it _{binding.name}"])

    def define(self, name: str, t: Type, kind: str, span: Span, mutable: bool = False, public: bool = False) -> Binding:
        existing = self.scope.names.get(name)
        if existing is not None and existing.kind not in ("builtin",) and not (self.repl and self.scope is self.globals):
            self.error(f"'{name}' is already defined in this scope", span,
                       notes=["use a different name, or assign to the existing variable if it's a var"])
        binding = Binding(name, t, kind, span, mutable=mutable, global_=self.scope is self.globals, public=public)
        if self.scope is self.globals:
            binding.used = True   # top-level names may be used by other modules
        self.scope.names[name] = binding
        return binding

    def check_stmt(self, stmt: A.Stmt) -> None:
        if isinstance(stmt, A.Let):
            declared = self.resolve_type(stmt.type) if stmt.type else None
            t = self.check_expr(stmt.value, declared)
            if declared is not None:
                self.expect(t, declared, stmt.value.span)
                t = declared
            elif prune(t) == NIL:
                self.error(f"can't tell what type '{stmt.name}' should have from nil", stmt.value.span,
                           notes=[f"give it a type: {'var' if stmt.mutable else 'let'} {stmt.name}: Int? = nil"])
                t = TypeVar()
            elif prune(t) == NEVER:
                t = TypeVar()
            self.define(stmt.name, t, "var" if stmt.mutable else "let", stmt.name_span, stmt.mutable, stmt.public)
        elif isinstance(stmt, A.Assign):
            self.check_assign(stmt)
        elif isinstance(stmt, A.ExprStmt):
            expr = stmt.expr
            if isinstance(expr, A.IfExpr):
                self.check_if(expr, None, statement=True)
            elif isinstance(expr, A.MatchExpr):
                self.check_match(expr, None, statement=True)
            else:
                t = self.check_expr(expr)
                if isinstance(expr, (A.Binary, A.Name, A.IntLit, A.StrLit, A.FloatLit, A.ListLit, A.Lambda)) and \
                        not (isinstance(expr, A.Binary) and expr.op == "??"):
                    self.warn("this value isn't used", expr.span)
                del t
        elif isinstance(stmt, A.Return):
            if self.fn is None:
                raise self.fail("'return' outside a function", stmt.span)
            if stmt.value is None:
                if prune(self.fn.ret) not in (NIL, NEVER) and not isinstance(prune(self.fn.ret), TypeVar):
                    self.error(f"'{self.fn.name}' must return {an(self.fn.ret)}", stmt.span)
                elif isinstance(prune(self.fn.ret), TypeVar):
                    unify(self.fn.ret, NIL)
            else:
                t = self.check_expr(stmt.value, self.fn.ret)
                self.expect(t, self.fn.ret, stmt.value.span, "wrong return type")
        elif isinstance(stmt, (A.Break, A.Continue)):
            word = "break" if isinstance(stmt, A.Break) else "continue"
            depth = self.fn.loop_depth if self.fn is not None else self._top_loops
            if depth == 0:
                self.error(f"'{word}' outside a loop", stmt.span)
        elif isinstance(stmt, A.While):
            self.expect(self.check_expr(stmt.cond, BOOL), BOOL, stmt.cond.span, "while condition")
            self.loop(lambda: self.check_block(stmt.body))
        elif isinstance(stmt, A.For):
            self.check_for(stmt)
        elif isinstance(stmt, A.FnDecl):
            # A nested function: usable from here on, including inside itself (recursion).
            self.declare_function(stmt, self.scope)
            self.check_function_body(stmt, self.scope)
        elif isinstance(stmt, (A.StructDecl, A.EnumDecl, A.ImplDecl, A.Import, A.TypeAlias)):
            self.error("this declaration belongs at the top level", stmt.span)
        else:  # pragma: no cover
            raise self.fail(f"unknown statement {type(stmt).__name__}", stmt.span)

    _top_loops = 0

    def loop(self, body: Callable[[], Any]) -> None:
        if self.fn is not None:
            self.fn.loop_depth += 1
        else:
            self._top_loops += 1
        try:
            body()
        finally:
            if self.fn is not None:
                self.fn.loop_depth -= 1
            else:
                self._top_loops -= 1

    def check_for(self, stmt: A.For) -> None:
        it = prune(self.check_expr(stmt.iterable))
        if it == RANGE:
            elem: Type = INT
        elif isinstance(it, ListT):
            elem = it.elem
        elif it == STR:
            elem = STR
        elif isinstance(it, MapT):
            elem = it.key
        elif isinstance(it, TypeVar):
            raise self.fail("can't tell what this loops over; give the collection a type", stmt.iterable.span)
        else:
            raise self.fail(f"can't loop over {an(it)}", stmt.iterable.span,
                            notes=["for loops work on ranges (0..n), lists, strings (by character) and maps (by key)"])
        saved = self.scope
        self.scope = Scope(saved)
        try:
            binding = self.define(stmt.var, elem, "let", stmt.var_span)
            binding.used = True
            self.loop(lambda: self.check_block(stmt.body))
        finally:
            self.scope = saved

    def check_assign(self, stmt: A.Assign) -> None:
        target = stmt.target
        if isinstance(target, A.Name):
            binding = self.scope.lookup(target.name)
            if binding is None:
                raise self.fail(f"'{target.name}' isn't defined", target.span,
                                notes=[f"declare it first: var {target.name} = ..."])
            target.binding = binding
            if binding.kind == "let":
                raise self.fail(f"can't assign to '{target.name}': it was declared with let", target.span,
                                notes=[f"declare it with var to change it: var {target.name} = ..."])
            if binding.kind == "param":
                raise self.fail(f"can't assign to the parameter '{target.name}'", target.span,
                                notes=[f"copy it first: var {target.name}2 = {target.name}"])
            if binding.kind != "var":
                raise self.fail(f"can't assign to '{target.name}'", target.span)
            target_type = binding.type
            target.type = target_type
        elif isinstance(target, A.Index):
            container = prune(self.check_expr(target.target))
            if isinstance(container, ListT):
                self.expect(self.check_expr(target.index, INT), INT, target.index.span, "list index")
                target_type = container.elem
            elif isinstance(container, MapT):
                self.expect(self.check_expr(target.index, container.key), container.key, target.index.span, "map key")
                target_type = container.value
            elif container == STR:
                raise self.fail("strings can't be changed in place", target.span,
                                notes=["build a new string, e.g. with slice() and +"])
            else:
                raise self.fail(f"can't assign to an element of {an(container)}", target.span)
            target.type = target_type
        else:
            assert isinstance(target, A.Field)
            target_type = self.check_field(target, assigning=True)
        if stmt.op == "=":
            value = self.check_expr(stmt.value, target_type)
            self.expect(value, target_type, stmt.value.span)
        else:
            op = stmt.op[0]
            value = self.check_expr(stmt.value, target_type)
            result = self.binary_type(op, target_type, value, stmt.span, stmt.target.span, stmt.value.span)
            self.expect(result, target_type, stmt.span, f"'{stmt.op}'")

    # -- functions -------------------------------------------------------------------------------

    def check_function_body(self, decl: A.FnDecl, scope: Scope) -> None:
        binding: Binding = decl.info
        fn_type = prune(binding.type)
        assert isinstance(fn_type, FnT)
        saved_scope, saved_fn = self.scope, self.fn
        self.scope = Scope(scope, "function")
        for tp in binding.type_params:
            self.scope.names[tp.name] = Binding(tp.name, tp, "type_param", None, extra=tp)
        if decl.has_self:
            target = binding.extra[0] if binding.extra else None
            self.scope.names["self"] = Binding("self", target, "param", decl.name_span, used=True)
        for p, t in zip(decl.params, fn_type.params):
            b = self.define(p.name, t, "param", p.span)
            b.used = True
        self.fn = FnContext(fn_type.ret, decl.name)
        try:
            self.check_function_block(decl.body, fn_type.ret, decl.name, decl.name_span)
        finally:
            self.scope, self.fn = saved_scope, saved_fn

    def check_function_block(self, body: A.Block, ret: Type, name: str, name_span: Span) -> None:
        wants_value = prune(ret) not in (NIL,) and not isinstance(prune(ret), TypeVar)
        if isinstance(prune(ret), TypeVar):
            # A lambda without a declared return type: the body decides.
            t = self.check_block(body, None, value=True)
            if prune(t) == NEVER or always_exits_block(body):
                unify(ret, NIL)
            elif not unify(ret, t):
                self.expect(t, ret, last_span(body), "the function's result")
            return
        tail = body.stmts[-1] if body.stmts else None
        # A trailing `if` without `else` can't be the function's value; treat it as a statement
        # so the error says what's actually wrong (a path without a return).
        tail_is_value = isinstance(tail, A.ExprStmt) and not (
            isinstance(tail.expr, A.IfExpr) and tail.expr.else_ is None)
        t = self.check_block(body, ret, value=wants_value and tail_is_value)
        if not wants_value:
            return
        if tail_is_value and prune(t) != NEVER:
            self.expect(t, ret, tail.expr.span, "wrong return type")
        elif not always_exits_block(body):
            self.error(f"'{name}' must return {an(ret)} on every path", name_span,
                       notes=["end the function with a value or a return statement"])

    def check_lambda(self, lam: A.Lambda, expected: Type | None) -> Type:
        exp = prune(expected) if expected is not None else None
        hint = exp if isinstance(exp, FnT) and len(exp.params) == len(lam.params) else None
        params: list[Type] = []
        for i, p in enumerate(lam.params):
            if p.type is not None:
                params.append(self.resolve_type(p.type))
            elif hint is not None:
                params.append(hint.params[i])
            else:
                raise self.fail(f"can't tell the type of parameter '{p.name}'", p.span,
                                notes=[f"write it: fn({p.name}: Int) ..."])
        if lam.ret is not None:
            ret = self.resolve_type(lam.ret)
        elif hint is not None and not has_unknowns(hint.ret):
            ret = hint.ret      # the context says what it returns, e.g. a field of type fn() -> Int?
        else:
            ret = TypeVar()
        saved_scope, saved_fn = self.scope, self.fn
        self.scope = Scope(saved_scope, "function")
        for p, t in zip(lam.params, params):
            b = self.define(p.name, t, "param", p.span)
            b.used = True
        self.fn = FnContext(ret, "this function")
        try:
            self.check_function_block(lam.body, ret, "this function", lam.span)
        finally:
            self.scope, self.fn = saved_scope, saved_fn
        result = FnT(tuple(params), ret)
        if hint is not None and ret is not hint.ret:
            # Let the expected type (e.g. map's fn(T) -> U) learn the result type.
            unify(hint.ret, ret)
        return result

    # -- expressions -------------------------------------------------------------------------------

    def check_expr(self, expr: A.Expr, expected: Type | None = None) -> Type:
        t = self._check_expr(expr, expected)
        expr.type = t
        return t

    def _check_expr(self, expr: A.Expr, expected: Type | None) -> Type:
        if isinstance(expr, A.IntLit):
            if not INT_MIN <= expr.value <= INT_MAX:
                self.error("integer is too big (Int is 64-bit)", expr.span)
            if expected is not None and prune(expected) == FLOAT:
                return INT   # expect() will explain how to write a Float
            return INT
        if isinstance(expr, A.FloatLit):
            return FLOAT
        if isinstance(expr, A.StrLit):
            return STR
        if isinstance(expr, A.BoolLit):
            return BOOL
        if isinstance(expr, A.NilLit):
            return NIL
        if isinstance(expr, A.Template):
            for part in expr.parts:
                if isinstance(part, A.Expr):
                    t = prune(self.check_expr(part))
                    if isinstance(t, FnT):
                        self.error("a function can't be put in a string", part.span)
            return STR
        if isinstance(expr, A.Name):
            return self.check_name(expr)
        if isinstance(expr, A.ListLit):
            exp = prune(expected) if expected is not None else None
            elem: Type = exp.elem if isinstance(exp, ListT) else TypeVar()
            if not expr.items and not isinstance(exp, ListT):
                self.pending_vars.append((elem, expr.span, "this empty list"))  # type: ignore[arg-type]
            for item in expr.items:
                t = self.check_expr(item, elem)
                if isinstance(prune(elem), TypeVar) and prune(t) == NIL:
                    elem_opt = OptionT(TypeVar())
                    unify(elem, elem_opt)
                self.expect(t, elem, item.span, "list item")
            return ListT(elem)
        if isinstance(expr, A.MapLit):
            exp = prune(expected) if expected is not None else None
            key: Type = exp.key if isinstance(exp, MapT) else TypeVar()
            value: Type = exp.value if isinstance(exp, MapT) else TypeVar()
            if not expr.entries and not isinstance(exp, MapT):
                self.pending_vars.append((key, expr.span, "this empty map"))  # type: ignore[arg-type]
            for k, v in expr.entries:
                kt = self.check_expr(k, key)
                self.expect(kt, key, k.span, "map key")
                if not is_hashable(kt) and not isinstance(prune(kt), TypeVar):
                    self.error(f"map keys must be Int, Str or Bool, not {resolve(kt)}", k.span)
                self.expect(self.check_expr(v, value), value, v.span, "map value")
            return MapT(key, value)
        if isinstance(expr, A.StructLit):
            return self.check_struct_lit(expr)
        if isinstance(expr, A.Unary):
            if expr.op == "not":
                self.expect(self.check_expr(expr.operand, BOOL), BOOL, expr.operand.span, "'not'")
                return BOOL
            t = prune(self.check_expr(expr.operand, expected))
            if not is_numeric(t):
                raise self.fail(f"'-' needs an Int or Float, found {resolve(t)}", expr.operand.span)
            if isinstance(expr.operand, A.IntLit) and -expr.operand.value < INT_MIN:
                self.error("integer is too small (Int is 64-bit)", expr.span)
            return t
        if isinstance(expr, A.Binary):
            return self.check_binary(expr, expected)
        if isinstance(expr, A.RangeExpr):
            self.expect(self.check_expr(expr.start, INT), INT, expr.start.span, "range start")
            self.expect(self.check_expr(expr.end, INT), INT, expr.end.span, "range end")
            return RANGE
        if isinstance(expr, A.Call):
            return self.check_call(expr, expected)
        if isinstance(expr, A.Index):
            return self.check_index(expr)
        if isinstance(expr, A.Field):
            return self.check_field(expr)
        if isinstance(expr, A.Lambda):
            return self.check_lambda(expr, expected)
        if isinstance(expr, A.IfExpr):
            return self.check_if(expr, expected, statement=False)
        if isinstance(expr, A.MatchExpr):
            return self.check_match(expr, expected, statement=False)
        if isinstance(expr, A.BlockExpr):
            return self.check_block(expr.block, expected, value=True)
        raise self.fail(f"unknown expression {type(expr).__name__}", expr.span)  # pragma: no cover

    def check_name(self, expr: A.Name) -> Type:
        binding = self.scope.lookup(expr.name)
        if binding is None:
            if expr.name == "self":
                raise self.fail("'self' only exists inside methods that take self", expr.span)
            close = difflib.get_close_matches(expr.name, self.scope.all_names(), n=1)
            raise self.fail(f"'{expr.name}' isn't defined", expr.span, [f"did you mean '{close[0]}'?"] if close else [])
        binding.used = True
        expr.binding = binding
        if binding.kind in ("struct", "enum"):
            what = "a struct" if binding.kind == "struct" else "an enum"
            hint = (f"create one with {expr.name} {{ ... }}" if binding.kind == "struct"
                    else f"use a variant, e.g. {expr.name}.{binding.extra.variants[0][0]}")
            raise self.fail(f"'{expr.name}' is {what}, not a value", expr.span, [hint])
        if binding.kind == "module":
            raise self.fail(f"'{expr.name}' is a module; use one of its members, e.g. {expr.name}.something", expr.span)
        if binding.kind in ("type_param", "alias"):
            raise self.fail(f"'{expr.name}' is a type, not a value", expr.span)
        if binding.kind == "builtin" and binding.extra.special:
            raise self.fail(f"'{expr.name}' must be called directly: {expr.name}(...)", expr.span)
        return self.instantiate(binding)

    def instantiate(self, binding: Binding) -> Type:
        if binding.type_params:
            mapping: dict[TypeParam, Type] = {tp: TypeVar() for tp in binding.type_params}
            return substitute(binding.type, mapping)
        return binding.type

    def check_struct_lit(self, expr: A.StructLit) -> Type:
        struct = self.type_from_path(expr.path)
        if not isinstance(struct, StructT):
            raise self.fail(f"'{describe_path(expr.path)}' isn't a struct", expr.path.span)
        seen: set[str] = set()
        for name, value, span in expr.fields:
            if name in seen:
                self.error(f"field '{name}' is given twice", span)
                continue
            seen.add(name)
            if name not in struct.fields:
                close = difflib.get_close_matches(name, list(struct.fields), n=1)
                self.error(f"{struct.name} has no field '{name}'", span, [f"did you mean '{close[0]}'?"] if close else [])
                self.check_expr(value)
                continue
            ftype = struct.fields[name]
            self.expect(self.check_expr(value, ftype), ftype, value.span, f"field '{name}'")
        missing = [f for f in struct.fields if f not in seen]
        if missing:
            self.error(f"missing field{'s' if len(missing) > 1 else ''} {', '.join(missing)} in {struct.name}",
                       expr.span)
        return struct

    def type_from_path(self, path: A.Expr) -> Type | None:
        if isinstance(path, A.Name):
            binding = self.scope.lookup(path.name)
            if binding is not None and binding.kind == "alias":
                binding.used = True
                return self.resolve_alias(binding)
            if binding is not None and binding.kind in ("struct", "enum"):
                binding.used = True
                path.binding = binding
                return binding.extra
            if binding is None:
                raise self.fail(f"'{path.name}' isn't defined", path.span)
            return None
        if isinstance(path, A.Field) and isinstance(path.target, A.Name):
            binding = self.scope.lookup(path.target.name)
            if binding is not None and binding.kind == "module":
                binding.used = True
                if binding.extra.broken:
                    raise CheckError()
                member = binding.extra.members.get(path.name)
                if member is not None and member.kind in ("struct", "enum"):
                    path.kind, path.extra = "module", member
                    return member.extra
                raise self.fail(f"module {path.target.name} has no public type '{path.name}'", path.span)
        return None

    # -- operators -------------------------------------------------------------------------------------

    def check_binary(self, expr: A.Binary, expected: Type | None) -> Type:
        op = expr.op
        if op in ("and", "or"):
            self.expect(self.check_expr(expr.left, BOOL), BOOL, expr.left.span, f"'{op}'")
            self.expect(self.check_expr(expr.right, BOOL), BOOL, expr.right.span, f"'{op}'")
            return BOOL
        if op == "??":
            left = prune(self.check_expr(expr.left))
            if not isinstance(left, OptionT):
                if left != NEVER:
                    self.warn(f"the left side of '??' is never nil (it's {an(left)})", expr.left.span)
                right = self.check_expr(expr.right, left)
                self.expect(right, left, expr.right.span)
                return left
            right = self.check_expr(expr.right, left.inner)
            if isinstance(prune(right), OptionT) or prune(right) == NIL:
                self.expect(right, left, expr.right.span)
                return left
            self.expect(right, left.inner, expr.right.span, "'??' default")
            return left.inner
        if op in ("==", "!="):
            left = self.check_expr(expr.left)
            right = self.check_expr(expr.right, left)
            lp, rp = prune(left), prune(right)
            if lp == NIL or rp == NIL:
                other, other_span = (rp, expr.right.span) if lp == NIL else (lp, expr.left.span)
                if not isinstance(other, (OptionT, TypeVar)) and other != NIL:
                    self.error(f"{an(other)} is never nil", other_span,
                               notes=["only optional values (T?) can be compared with nil"])
                return BOOL
            if not (self.fits(rp, lp) or self.fits(lp, rp)):
                self.error(f"can't compare {resolve(lp)} with {resolve(rp)}", expr.span)
            elif isinstance(lp, FnT):
                self.error("functions can't be compared", expr.span)
            return BOOL
        left = self.check_expr(expr.left, expected if op in "+-*/%" else None)
        right = self.check_expr(expr.right, left)
        return self.binary_type(op, left, right, expr.span, expr.left.span, expr.right.span)

    def binary_type(self, op: str, left: Type, right: Type, span: Span, lspan: Span, rspan: Span) -> Type:
        lp, rp = prune(left), prune(right)
        if isinstance(lp, TypeVar) and not isinstance(rp, TypeVar):
            unify(lp, rp)
            lp = rp
        elif isinstance(rp, TypeVar):
            unify(rp, lp)
            rp = lp
        if lp == NEVER or rp == NEVER:
            return rp if lp == NEVER else lp
        if op in ("<", "<=", ">", ">="):
            if lp != rp or not is_comparable(lp):
                raise self.fail(f"'{op}' can't compare {resolve(lp)} and {resolve(rp)}", span,
                                notes=mixed_note(lp, rp) or ["< and > work on two Ints, two Floats or two Strs"])
            return BOOL
        if lp != rp and not (isinstance(lp, ListT) and isinstance(rp, ListT) and unify(lp, rp)):
            raise self.fail(f"'{op}' needs two values of the same type, found {resolve(lp)} and {resolve(rp)}", span,
                            notes=mixed_note(lp, rp))
        if op == "+":
            if lp in (INT, FLOAT, STR) or isinstance(lp, ListT):
                return lp
            raise self.fail(f"'+' doesn't work on {resolve(lp)}", span,
                            notes=["+ adds numbers and joins strings or lists"])
        if lp in (INT, FLOAT):
            return lp
        raise self.fail(f"'{op}' needs Int or Float, found {resolve(lp)}", span)

    # -- calls ---------------------------------------------------------------------------------------------

    def check_call(self, expr: A.Call, expected: Type | None) -> Type:
        callee = expr.callee
        if isinstance(callee, A.Name):
            binding = self.scope.lookup(callee.name)
            if binding is not None and binding.kind == "builtin":
                binding.used = True
                callee.binding = binding
                expr.kind = "builtin"
                return binding.extra.check(self, expr, expected)
            if binding is not None and binding.kind == "struct":
                raise self.fail(f"create a {callee.name} with {callee.name} {{ field: value, ... }}", expr.span)
        if isinstance(callee, A.Field):
            handled = self.check_method_call(expr, callee, expected)
            if handled is not None:
                return handled
        fn = prune(self.check_expr(callee))
        if expr.kind == "":
            expr.kind = "function"
        if isinstance(fn, TypeVar):
            raise self.fail("can't tell what kind of function this is", callee.span)
        if not isinstance(fn, FnT):
            raise self.fail(f"{an(fn)} can't be called", callee.span)
        return self.check_args(expr, fn, describe_callee(callee))

    def check_args(self, expr: A.Call, fn: FnT, name: str, skip: int = 0) -> Type:
        params = fn.params[skip:]
        if len(expr.args) != len(params):
            s = "" if len(params) == 1 else "s"
            raise self.fail(f"{name} takes {len(params)} argument{s}, but {len(expr.args)} were given", expr.span,
                            notes=[f"its type is {resolve(FnT(params, fn.ret))}"])
        for arg, param in zip(expr.args, params):
            t = self.check_expr(arg, param)
            self.expect(t, param, arg.span, f"argument to {name}")
        return fn.ret

    def check_method_call(self, expr: A.Call, callee: A.Field, expected: Type | None) -> Type | None:
        """Calls written as a.b(...): methods, static methods, enum variants and module functions."""
        target = callee.target
        # Type-level: Point.origin(), Shape.Circle(1.0), geo.area(...), geo.Shape.Circle(1.0)
        type_t = self.type_target(target)
        if type_t is not None:
            if isinstance(type_t, EnumT):
                found = type_t.variant(callee.name)
                if found is not None:
                    index, fields = found
                    if not fields:
                        raise self.fail(f"{type_t.name}.{callee.name} has no fields; write it without ( )", expr.span)
                    callee.kind, callee.extra = "variant", (type_t, index)
                    expr.kind, expr.extra = "variant", (type_t, index)
                    callee.type = FnT(fields, type_t)
                    self.check_args(expr, FnT(fields, type_t), f"{type_t.name}.{callee.name}")
                    return type_t
            method = type_t.methods.get(callee.name)
            if method is None:
                raise self.fail(f"{type_t.name} has no method or variant '{callee.name}'", callee.name_span,
                                notes=self.member_suggestion(type_t, callee.name))
            if method.has_self:
                raise self.fail(f"'{callee.name}' takes self; call it on a value: value.{callee.name}(...)",
                                callee.name_span)
            fn = self.instantiate_method(method)
            callee.kind, callee.extra = "static", (type_t, method)
            expr.kind, expr.extra = "static", (type_t, method)
            callee.type = fn
            return self.check_args(expr, fn, f"{type_t.name}.{callee.name}")
        if isinstance(target, A.Name):
            binding = self.scope.lookup(target.name)
            if binding is not None and binding.kind == "module":
                return None   # module functions are ordinary values: geo.area is a Field of kind "module"
        receiver = prune(self.check_expr(target))
        if isinstance(receiver, (StructT, EnumT)):
            method = receiver.methods.get(callee.name)
            if method is not None:
                if not method.has_self:
                    raise self.fail(f"'{callee.name}' doesn't take self; call it as {receiver.name}.{callee.name}(...)",
                                    callee.name_span)
                fn = self.instantiate_method(method)
                callee.kind, callee.extra = "method", (receiver, method)
                expr.kind, expr.extra = "method", (receiver, method)
                callee.type = fn
                return self.check_args(expr, fn, f"{receiver.name}.{callee.name}")
            if isinstance(receiver, StructT) and callee.name in receiver.fields:
                return None   # calling a field that holds a function: (p.handler)(x)
            raise self.fail(f"{receiver.name} has no method '{callee.name}'", callee.name_span,
                            notes=self.member_suggestion(receiver, callee.name))
        if isinstance(receiver, OptionT):
            raise self.fail(f"can't call .{callee.name}() on a value that might be nil ({resolve(receiver)})",
                            target.span, notes=["unwrap it first with `if let`, `??` or `match`"])
        if isinstance(receiver, TypeVar):
            raise self.fail(f"can't tell the type of this value to find .{callee.name}()", target.span,
                            notes=["give the variable a type where it's declared"])
        sig = builtin_method(receiver, callee.name)
        if sig is None:
            from .builtins import method_names
            names = method_names(receiver)
            close = difflib.get_close_matches(callee.name, names, n=1)
            notes = [f"did you mean .{close[0]}()?"] if close else ([f"{resolve(receiver)} methods: {', '.join(names)}"]
                                                                     if names else [])
            raise self.fail(f"{resolve(receiver)} has no method '{callee.name}'", callee.name_span, notes)
        callee.kind = "builtin_method"
        expr.kind = "builtin_method"
        expr.extra = callee.name
        callee.type = sig.type
        result = self.check_args(expr, sig.type, f".{callee.name}()")
        if callee.name == "sum" and isinstance(receiver, ListT) and prune(receiver.elem) == FLOAT:
            expr.extra = "fsum"   # an empty List[Float] sums to 0.0, not the Int 0
        if sig.after is not None:
            problem = sig.after(receiver)
            if problem:
                self.error(problem, expr.span)
        return result

    def member_suggestion(self, t: StructT | EnumT, name: str) -> list[str]:
        names = list(t.methods) + (list(t.fields) if isinstance(t, StructT) else [v for v, _ in t.variants])
        close = difflib.get_close_matches(name, names, n=1)
        return [f"did you mean '{close[0]}'?"] if close else []

    def instantiate_method(self, method: Method) -> FnT:
        if method.type_params:
            fn = substitute(method.type, {tp: TypeVar() for tp in method.type_params})
            assert isinstance(fn, FnT)
            return fn
        return method.type

    def type_target(self, target: A.Expr) -> StructT | EnumT | None:
        """If ``target`` names a struct or enum (Point, geo.Shape), returns it."""
        if isinstance(target, A.Name):
            binding = self.scope.lookup(target.name)
            if binding is not None and binding.kind == "alias":
                t = self.resolve_alias(binding)
                binding.used = True
                return t if isinstance(t, (StructT, EnumT)) else None
            if binding is not None and binding.kind in ("struct", "enum"):
                binding.used = True
                target.binding = binding
                return binding.extra
            return None
        if isinstance(target, A.Field) and isinstance(target.target, A.Name):
            binding = self.scope.lookup(target.target.name)
            if binding is not None and binding.kind == "module":
                if binding.extra.broken:
                    raise CheckError()
                member = binding.extra.members.get(target.name)
                if member is not None and member.kind in ("struct", "enum"):
                    binding.used = True
                    target.kind, target.extra = "module", member
                    return member.extra
        return None

    # -- fields and indexing ---------------------------------------------------------------------------------

    def check_field(self, expr: A.Field, assigning: bool = False) -> Type:
        target = expr.target
        type_t = self.type_target(target)
        if type_t is not None:
            if assigning:
                raise self.fail("can't assign to a type's member", expr.span)
            if isinstance(type_t, EnumT):
                found = type_t.variant(expr.name)
                if found is not None:
                    index, fields = found
                    expr.kind, expr.extra = "variant", (type_t, index)
                    return FnT(fields, type_t) if fields else type_t
            method = type_t.methods.get(expr.name)
            if method is not None and not method.has_self:
                expr.kind, expr.extra = "static", (type_t, method)
                return self.instantiate_method(method)
            raise self.fail(f"{type_t.name} has no variant or static method '{expr.name}'", expr.name_span,
                            notes=self.member_suggestion(type_t, expr.name))
        if isinstance(target, A.Name):
            binding = self.scope.lookup(target.name)
            if binding is not None and binding.kind == "module":
                binding.used = True
                target.binding = binding
                if binding.extra.broken:
                    raise CheckError()
                member = binding.extra.members.get(expr.name)
                if member is None:
                    raise self.fail(f"module {target.name} has no public member '{expr.name}'", expr.name_span,
                                    notes=["only declarations marked pub can be used from other files"])
                if member.kind in ("struct", "enum"):
                    raise self.fail(f"'{target.name}.{expr.name}' is a type, not a value", expr.span)
                if assigning:
                    raise self.fail("can't assign to another module's variables", expr.span)
                expr.kind, expr.extra = "module", member
                return self.instantiate(member)
        receiver = prune(self.check_expr(target))
        if isinstance(receiver, StructT):
            if expr.name in receiver.fields:
                expr.kind, expr.extra = "field", receiver.field_index(expr.name)
                return receiver.fields[expr.name]
            if expr.name in receiver.methods:
                raise self.fail(f"'{expr.name}' is a method; call it: {describe_path(target)}.{expr.name}(...)",
                                expr.name_span)
            raise self.fail(f"{receiver.name} has no field '{expr.name}'", expr.name_span,
                            notes=self.member_suggestion(receiver, expr.name))
        if isinstance(receiver, OptionT):
            raise self.fail(f"can't use .{expr.name} on a value that might be nil ({resolve(receiver)})", target.span,
                            notes=["unwrap it first with `if let`, `??` or `match`"])
        if builtin_method(receiver, expr.name) is not None or isinstance(receiver, EnumT) and expr.name in receiver.methods:
            raise self.fail(f"'{expr.name}' is a method; call it: .{expr.name}()", expr.name_span)
        raise self.fail(f"{an(receiver)} has no field '{expr.name}'", expr.name_span)

    def check_index(self, expr: A.Index) -> Type:
        container = prune(self.check_expr(expr.target))
        if isinstance(container, ListT):
            self.expect(self.check_expr(expr.index, INT), INT, expr.index.span, "list index")
            return container.elem
        if container == STR:
            self.expect(self.check_expr(expr.index, INT), INT, expr.index.span, "string index")
            return STR
        if isinstance(container, MapT):
            self.expect(self.check_expr(expr.index, container.key), container.key, expr.index.span, "map key")
            return container.value
        if isinstance(container, OptionT):
            raise self.fail(f"can't index a value that might be nil ({resolve(container)})", expr.target.span,
                            notes=["unwrap it first with `if let`, `??` or `match`"])
        raise self.fail(f"{an(container)} can't be indexed", expr.target.span)

    # -- if and match -------------------------------------------------------------------------------------------

    def join(self, a: Type, b: Type, span: Span) -> Type:
        """The type of a value that is either an a or a b (the branches of an if or match)."""
        a, b = prune(a), prune(b)
        if a == NEVER:
            return b
        if b == NEVER:
            return a
        if a == NIL and b != NIL:
            return b if isinstance(b, OptionT) else (OptionT(b) if not isinstance(b, TypeVar) else b)
        if b == NIL and a != NIL:
            return a if isinstance(a, OptionT) else (OptionT(a) if not isinstance(a, TypeVar) else a)
        if isinstance(a, OptionT) and not isinstance(b, OptionT):
            self.expect(b, a, span)
            return a
        if isinstance(b, OptionT) and not isinstance(a, OptionT):
            self.expect(a, b, span)
            return b
        if not unify(a, b):
            self.error(f"the branches have different types: {resolve(a)} and {resolve(b)}", span)
        return a

    def check_if(self, expr: A.IfExpr, expected: Type | None, statement: bool) -> Type:
        saved = self.scope
        if expr.let_name is not None:
            t = prune(self.check_expr(expr.cond))
            if not isinstance(t, OptionT):
                self.error(f"'if let' needs an optional value (T?), found {resolve(t)}", expr.cond.span,
                           notes=["use a plain `if` and a `let` inside it"])
                inner = t
            else:
                inner = t.inner
            self.scope = Scope(saved)
            b = self.define(expr.let_name, inner, "let", expr.let_span or expr.span)
            b.used = True
        else:
            self.expect(self.check_expr(expr.cond, BOOL), BOOL, expr.cond.span, "if condition")
        try:
            then_t = self.check_block(expr.then, expected, value=not statement)
        finally:
            self.scope = saved
        if expr.else_ is None:
            if not statement and expected is not None and prune(expected) not in (NIL,) and \
                    not isinstance(prune(expected), (OptionT, TypeVar)):
                self.error("this if has no else, so it has no value when the condition is false", expr.span,
                           notes=["add an else branch"])
            return NIL if statement else self.join(then_t, NIL, expr.span)
        if isinstance(expr.else_, A.IfExpr):
            else_t = self.check_if(expr.else_, expected, statement)
            expr.else_.type = else_t
        else:
            else_t = self.check_block(expr.else_, expected or (then_t if not statement else None), value=not statement)
        return NIL if statement else self.join(then_t, else_t, expr.else_.span)

    def check_match(self, expr: A.MatchExpr, expected: Type | None, statement: bool) -> Type:
        subject = prune(self.check_expr(expr.subject))
        if isinstance(subject, TypeVar):
            raise self.fail("can't tell the type of the value being matched", expr.subject.span)
        result: Type | None = None
        nil_covered = False
        catch_all_seen = False
        for arm in expr.arms:
            if catch_all_seen:
                self.warn("this arm is never reached: an earlier arm matches everything", arm.pattern.span)
            saved = self.scope
            self.scope = Scope(saved)
            try:
                self.check_pattern(arm.pattern, subject, narrow=nil_covered)
                if arm.guard is not None:
                    self.expect(self.check_expr(arm.guard, BOOL), BOOL, arm.guard.span, "match guard")
                if statement:
                    if isinstance(arm.body, A.BlockExpr):
                        self.check_block(arm.body.block)
                        arm.body.type = NIL
                    else:
                        self.check_expr(arm.body)
                else:
                    t = self.check_expr(arm.body, expected or result)
                    result = t if result is None else self.join(result, t, arm.body.span)
            except CheckError:
                if result is None and not statement:
                    result = TypeVar()   # the error is reported; don't add a confusing "found Nil" after it
            finally:
                self.scope = saved
            if arm.guard is None:
                if isinstance(arm.pattern, A.LitPat) and isinstance(arm.pattern.value, A.NilLit):
                    nil_covered = True
                if irrefutable(arm.pattern):
                    catch_all_seen = True
        rows = [[arm.pattern] for arm in expr.arms if arm.guard is None]
        missing = missing_cases([subject], rows)
        if missing is not None:
            example = missing[0]
            self.error(f"this match doesn't cover every case: {example} isn't handled", expr.subject.span,
                       notes=["add an arm for it, or a catch-all arm: _ => ..."])
        return NIL if statement or result is None else result

    def check_pattern(self, pat: A.Pattern, t: Type, narrow: bool = False) -> None:
        t = prune(t)
        if isinstance(pat, A.WildcardPat):
            return
        if isinstance(pat, A.OrPat):
            for option in pat.options:
                if binds_names(option):
                    raise self.fail("patterns joined with | can't bind names yet", option.span,
                                    notes=["use _ in the alternatives, or give each one its own arm"])
                self.check_pattern(option, t, narrow)
            return
        if isinstance(pat, A.StructPat):
            struct = t.inner if isinstance(t, OptionT) else t
            pat.nullable = isinstance(t, OptionT)
            if not isinstance(struct, StructT) or struct.name != pat.name:
                raise self.fail(f"'{pat.name} {{ ... }}' is a struct pattern, but the value is {an(t)}", pat.span)
            pat.resolved = struct
            seen: set[str] = set()
            for fname, sub, fspan in pat.fields:
                if fname in seen:
                    raise self.fail(f"field '{fname}' appears twice in the pattern", fspan)
                seen.add(fname)
                if fname not in struct.fields:
                    raise self.fail(f"{struct.name} has no field '{fname}'", fspan,
                                    notes=self.member_suggestion(struct, fname))
                self.check_pattern(sub, struct.fields[fname])
            missing = [f for f in struct.fields if f not in seen]
            if missing and not pat.rest:
                raise self.fail(f"the pattern doesn't mention {', '.join(missing)}", pat.span,
                                notes=["add them, or end the pattern with .. to ignore the rest"])
            return
        if isinstance(pat, A.RangePat):
            target = t.inner if isinstance(t, OptionT) else t
            pat.nullable = isinstance(t, OptionT)
            if target != INT:
                raise self.fail(f"range patterns match Ints, but the value is {an(t)}", pat.span)
            if pat.high < pat.low or (pat.high == pat.low and not pat.inclusive):
                self.warn("this range is empty, so the arm never matches", pat.span)
            return
        if isinstance(pat, A.BindPat):
            bound = t.inner if narrow and isinstance(t, OptionT) else t
            b = self.define(pat.name, bound, "let", pat.span)
            b.used = True
            return
        if isinstance(pat, A.LitPat):
            lit_t = self.check_expr(pat.value)
            if prune(lit_t) == NIL:
                if not isinstance(t, OptionT):
                    self.error(f"{an(t)} is never nil", pat.span)
                return
            target = t.inner if isinstance(t, OptionT) else t
            if prune(lit_t) == FLOAT:
                self.warn("matching exact Float values is fragile", pat.span,
                          notes=["compare with a tolerance in a guard instead: x if (x - 0.1).abs() < 0.0001"])
            self.expect(lit_t, target, pat.span, "pattern")
            return
        assert isinstance(pat, A.VariantPat)
        enum = t.inner if isinstance(t, OptionT) else t
        if not isinstance(enum, EnumT):
            raise self.fail(f"'{pat.variant}' is a variant pattern, but the value is {an(t)}", pat.span,
                            notes=["variant names start with a capital letter; use a lowercase name to bind the value"])
        if pat.enum is not None and pat.enum != enum.name:
            raise self.fail(f"expected a {enum.name} variant, found {pat.enum}.{pat.variant}", pat.span)
        found = enum.variant(pat.variant)
        if found is None:
            close = difflib.get_close_matches(pat.variant, [v for v, _ in enum.variants], n=1)
            raise self.fail(f"{enum.name} has no variant '{pat.variant}'", pat.span,
                            [f"did you mean {close[0]}?"] if close else [f"variants: {', '.join(v for v, _ in enum.variants)}"])
        index, fields = found
        pat.resolved = (enum, index)
        args = pat.args
        if args is None:
            if fields:
                underscores = ", ".join("_" for _ in fields)
                raise self.fail(f"{pat.variant} has {len(fields)} field{'s' if len(fields) > 1 else ''}; "
                                f"write {pat.variant}({underscores})", pat.span)
            return
        if len(args) != len(fields):
            raise self.fail(f"{pat.variant} has {len(fields)} field{'s' if len(fields) != 1 else ''}, "
                            f"but the pattern has {len(args)}", pat.span)
        for sub, ftype in zip(args, fields):
            self.check_pattern(sub, ftype)


# -- helpers ---------------------------------------------------------------------------------------------------


def an(t: Type) -> str:
    """'an Int', 'a Str', 'a List[Int]'."""
    text = str(resolve(t))
    return f"{'an' if text[:1] in 'AEIOU' else 'a'} {text}"


def irrefutable(pat: A.Pattern) -> bool:
    if isinstance(pat, A.OrPat):
        return any(irrefutable(p) for p in pat.options)
    return isinstance(pat, (A.WildcardPat, A.BindPat))


def binds_names(pat: A.Pattern) -> bool:
    if isinstance(pat, A.BindPat):
        return True
    if isinstance(pat, A.OrPat):
        return any(binds_names(p) for p in pat.options)
    if isinstance(pat, A.VariantPat):
        return any(binds_names(p) for p in pat.args or [])
    if isinstance(pat, A.StructPat):
        return any(binds_names(p) for _, p, _ in pat.fields)
    return False


def expand_or(rows: list[list[A.Pattern]]) -> list[list[A.Pattern]]:
    """A row starting with a | b becomes a row for a and a row for b."""
    out: list[list[A.Pattern]] = []
    for row in rows:
        if row and isinstance(row[0], A.OrPat):
            out.extend(expand_or([[option] + row[1:] for option in row[0].options]))
        else:
            out.append(row)
    return out


def missing_cases(types: list[Type], rows: list[list[A.Pattern]]) -> list[str] | None:
    """Exhaustiveness: None if the rows of patterns cover every value of ``types``,
    otherwise an example of an uncovered value (one string per column)."""
    rows = expand_or(rows)
    if not types:
        return None if rows else []
    if not rows:
        return ["_"] * len(types)   # nothing matches here at all, so anything is an example
    if any(all(irrefutable(p) for p in row) for row in rows):
        return None          # a row of catch-alls covers everything that's left
    first = prune(types[0])
    rest = types[1:]
    if rows and all(irrefutable(row[0]) for row in rows):
        # Nothing looks inside this value, so don't expand it (recursive enums would never end).
        found = missing_cases(rest, [row[1:] for row in rows])
        return None if found is None else ["_"] + found

    def sub_missing(ctor_types: list[Type], specialized: list[list[A.Pattern]]) -> list[str] | None:
        return missing_cases(ctor_types + rest, specialized)

    if isinstance(first, EnumT):
        for index, (name, fields) in enumerate(first.variants):
            specialized = []
            for row in rows:
                pat = row[0]
                if irrefutable(pat):
                    specialized.append([A.WildcardPat(pat.span)] * len(fields) + row[1:])
                elif isinstance(pat, A.VariantPat) and pat.variant == name and len(pat.args or []) == len(fields):
                    # (patterns with the wrong number of fields were reported already; leave them out)
                    specialized.append(list(pat.args or []) + row[1:])
            found = sub_missing(list(fields), specialized)
            if found is not None:
                args = found[:len(fields)]
                shown = f"{first.name}.{name}" + (f"({', '.join(args)})" if fields else "")
                return [shown] + found[len(fields):]
        return None
    if isinstance(first, StructT):
        # One shape: look at the fields, in declaration order.
        names = list(first.fields)
        specialized = []
        for row in rows:
            pat = row[0]
            if irrefutable(pat):
                specialized.append([A.WildcardPat(pat.span)] * len(names) + row[1:])
            elif isinstance(pat, A.StructPat):
                given = {name: sub for name, sub, _ in pat.fields}
                specialized.append([given.get(n, A.WildcardPat(pat.span)) for n in names] + row[1:])
        found = sub_missing(list(first.fields.values()), specialized)
        if found is not None:
            inner = ", ".join(f"{n}: {v}" for n, v in zip(names, found[:len(names)]))
            return [f"{first.name} {{ {inner} }}"] + found[len(names):]
        return None
    if first == BOOL:
        for value in (True, False):
            specialized = [row[1:] for row in rows if irrefutable(row[0]) or (
                isinstance(row[0], A.LitPat) and isinstance(row[0].value, A.BoolLit) and row[0].value.value == value)]
            found = sub_missing([], specialized)
            if found is not None:
                return ["true" if value else "false"] + found
        return None
    if isinstance(first, OptionT):
        nil_rows = [row[1:] for row in rows if irrefutable(row[0]) or (
            isinstance(row[0], A.LitPat) and isinstance(row[0].value, A.NilLit))]
        found = sub_missing([], nil_rows)
        if found is not None:
            return ["nil"] + found
        some_rows = [([A.WildcardPat(row[0].span)] if irrefutable(row[0]) else [row[0]]) + row[1:]
                     for row in rows if not (isinstance(row[0], A.LitPat) and isinstance(row[0].value, A.NilLit))]
        return sub_missing([first.inner], some_rows)
    # Ints, strings, floats...: only a catch-all covers everything.
    default = [row[1:] for row in rows if irrefutable(row[0])]
    found = missing_cases(rest, default)
    if found is not None:
        # "_" when nothing looks at this value; "another Int" when some literals do.
        some_literals = any(isinstance(row[0], (A.LitPat, A.RangePat)) for row in rows)
        shown = f"another {first}" if some_literals and first in (INT, STR, FLOAT) else "_"
        return [shown] + found
    return None


def always_exits(stmt: A.Stmt) -> bool:
    """True if the statement never finishes normally (it returns or panics on every path)."""
    if isinstance(stmt, A.Return):
        return True
    if isinstance(stmt, A.ExprStmt):
        expr = stmt.expr
        if isinstance(expr, A.Call) and expr.type is not None and prune(expr.type) == NEVER:
            return True
        if isinstance(expr, A.IfExpr):
            return expr.else_ is not None and always_exits_block(expr.then) and (
                always_exits_block(expr.else_) if isinstance(expr.else_, A.Block)
                else always_exits(A.ExprStmt(expr.else_.span, expr.else_)))
        if isinstance(expr, A.MatchExpr):
            return all(isinstance(arm.body, A.BlockExpr) and always_exits_block(arm.body.block) or
                       (isinstance(arm.body, A.Call) and arm.body.type is not None and prune(arm.body.type) == NEVER)
                       for arm in expr.arms)
    return False


def always_exits_block(block: A.Block) -> bool:
    return any(always_exits(s) for s in block.stmts)


def last_span(block: A.Block) -> Span:
    return block.stmts[-1].span if block.stmts else block.span


def mixed_note(a: Type, b: Type) -> list[str]:
    if isinstance(a, OptionT) or isinstance(b, OptionT):
        return ["one side might be nil; unwrap it first with `if let`, `??` or `match`"]
    if {a, b} == {INT, FLOAT}:
        return ["convert the Int with float(...) first"]
    if STR in (a, b) and (a in (INT, FLOAT) or b in (INT, FLOAT)):
        return ["convert the number with str(...), or use a template string: \"${...}\""]
    return []


def describe_path(expr: A.Expr) -> str:
    if isinstance(expr, A.Name):
        return expr.name
    if isinstance(expr, A.Field):
        return f"{describe_path(expr.target)}.{expr.name}"
    return "value"


def describe_callee(expr: A.Expr) -> str:
    if isinstance(expr, (A.Name, A.Field)):
        return f"'{describe_path(expr)}'"
    return "this function"


def check(program: A.Program, source: Source, module_name: str = "main",
          import_module: Callable[[A.Import], ModuleT] | None = None) -> Checker:
    """Type-checks a program. Raises EmberError listing every error; returns the checker
    (with ``warnings`` and ``globals``) if there are none."""
    checker = Checker(source, module_name, import_module)
    checker.check_program(program)
    position = lambda d: d.span.start if d.span else -1  # noqa: E731
    checker.errors.sort(key=position)
    checker.warnings.sort(key=position)
    if checker.errors:
        raise EmberError(checker.errors, source)
    return checker


__all__ = ["Checker", "Binding", "check", "Prim"]
