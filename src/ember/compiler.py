"""Compiles a checked syntax tree into bytecode.

One :class:`FunctionCompiler` per function (the top level of a file is the
function ``<main>``). Locals get numbered slots; a slot is reused once its scope
ends. Variables captured by a closure become upvalues, as in Lua: the closure
points at the slot while the frame is alive, and the slot is "closed" (its value
copied into the upvalue) when the scope ends, so each loop iteration's
closures see their own variable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import ast as A
from .bytecode import Op
from .checker import Binding
from .source import Source, Span
from .types import FLOAT, INT, STR, EnumT, ListT, StructT, prune
from .values import EnumType, EnumValue, FunctionProto, NativeFn, StructType


class CompileError(Exception):
    pass


@dataclass
class Local:
    name: str
    slot: int
    depth: int
    captured: bool = False


@dataclass
class Loop:
    continue_target: int
    depth: int
    breaks: list[int] = field(default_factory=list)
    continues: list[int] = field(default_factory=list)
    is_for: bool = False


def runtime_struct(t: StructT) -> StructType:
    rt = getattr(t, "_runtime", None)
    if rt is None:
        rt = StructType(t.name, list(t.fields))
        t._runtime = rt  # type: ignore[attr-defined]
    return rt


def runtime_enum(t: EnumT) -> EnumType:
    rt = getattr(t, "_runtime", None)
    if rt is None:
        rt = EnumType(t.name, [(name, len(fields)) for name, fields in t.variants])
        t._runtime = rt  # type: ignore[attr-defined]
    return rt


def method_key(t: StructT | EnumT, name: str) -> tuple[str, str]:
    return (t.module, f"{t.name}.{name}")


class FunctionCompiler:
    def __init__(self, name: str, arity: int, parent: FunctionCompiler | None, module: str, source: Source):
        self.proto = FunctionProto(name, arity, module=module, source_name=source.name)
        self.parent = parent
        self.module = module
        self.source = source
        self.locals: list[Local] = []
        self.depth = 0
        self.next_slot = 0
        self.upvalue_index: dict[tuple[bool, int], int] = {}
        self.loops: list[Loop] = []
        self.line = 1
        self._constants: dict[Any, int] = {}

    # -- emitting ----------------------------------------------------------------------------

    def emit(self, op: Op, *args: int) -> int:
        at = len(self.proto.code)
        self.proto.code.append(int(op))
        self.proto.lines.append(self.line)
        for arg in args:
            self.proto.code.append(arg)
            self.proto.lines.append(self.line)
        return at

    def emit_jump(self, op: Op) -> int:
        """Emits a jump with a placeholder target; returns where the target goes."""
        at = self.emit(op, -1)
        return at + 1

    def patch(self, operand_at: int, target: int | None = None) -> None:
        self.proto.code[operand_at] = len(self.proto.code) if target is None else target

    def here(self) -> int:
        return len(self.proto.code)

    def constant(self, value: Any) -> int:
        key: Any
        try:
            key = (type(value), value) if not isinstance(value, (FunctionProto, StructType, EnumType, EnumValue,
                                                                 NativeFn)) else id(value)
            hash(key)
        except TypeError:
            key = id(value)
        if key in self._constants:
            return self._constants[key]
        self.proto.constants.append(value)
        self._constants[key] = len(self.proto.constants) - 1
        return self._constants[key]

    def at(self, span: Span) -> None:
        self.line = self.source.line_col(span.start)[0]

    # -- scopes and variables ---------------------------------------------------------------------

    def begin_scope(self) -> None:
        self.depth += 1

    def end_scope(self) -> None:
        self.close_scopes_above(self.depth - 1)
        while self.locals and self.locals[-1].depth >= self.depth:
            self.locals.pop()
        self.reset_next_slot()
        self.depth -= 1

    def reset_next_slot(self) -> None:
        # Pattern bindings are aliases of existing slots, so take the highest slot still in use.
        self.next_slot = max((local.slot for local in self.locals), default=self.proto.arity - 1) + 1

    def close_scopes_above(self, depth: int) -> None:
        """Closes captured locals deeper than ``depth`` (before leaving those scopes)."""
        for local in reversed(self.locals):
            if local.depth <= depth:
                break
            if local.captured:
                self.emit(Op.CLOSE_UPVALUE, local.slot)

    def add_local(self, name: str) -> int:
        slot = self.next_slot
        self.next_slot += 1
        self.proto.max_locals = max(self.proto.max_locals, self.next_slot)
        self.locals.append(Local(name, slot, self.depth))
        return slot

    def temp(self) -> int:
        """A hidden local for intermediate values (match subjects, compound assignments)."""
        return self.add_local(" temp")

    def find_local(self, name: str) -> Local | None:
        for local in reversed(self.locals):
            if local.name == name:
                return local
        return None

    def find_upvalue(self, name: str) -> int | None:
        if self.parent is None:
            return None
        local = self.parent.find_local(name)
        if local is not None:
            local.captured = True
            return self.add_upvalue(True, local.slot)
        index = self.parent.find_upvalue(name)
        if index is not None:
            return self.add_upvalue(False, index)
        return None

    def add_upvalue(self, is_local: bool, index: int) -> int:
        key = (is_local, index)
        if key not in self.upvalue_index:
            self.proto.upvalues.append(key)
            self.upvalue_index[key] = len(self.proto.upvalues) - 1
        return self.upvalue_index[key]


class Compiler:
    def __init__(self, source: Source, module: str = "main"):
        self.source = source
        self.module = module
        self.fc: FunctionCompiler = FunctionCompiler("<main>", 0, None, module, source)
        self.main = self.fc

    # -- entry ------------------------------------------------------------------------------------

    def compile_program(self, program: A.Program, return_last: bool = False) -> FunctionProto:
        """``return_last`` (for the REPL): a final expression statement's value is returned instead of dropped."""
        fc = self.fc
        # Imports run first, then top-level functions and methods are defined, so both can be used anywhere.
        for stmt in program.stmts:
            if isinstance(stmt, A.Import):
                fc.at(stmt.span)
                fc.emit(Op.IMPORT, fc.constant(stmt.module_id))
                fc.emit(Op.DEFINE_GLOBAL, fc.constant(stmt.alias))
        for stmt in program.stmts:
            if isinstance(stmt, A.FnDecl):
                proto = self.compile_function(stmt.name, stmt.params, stmt.body, stmt, has_self=False,
                                              top_level=True)
                fc.at(stmt.span)
                fc.emit(Op.CLOSURE, fc.constant(proto))
                fc.emit(Op.DEFINE_GLOBAL, fc.constant(stmt.name))
            elif isinstance(stmt, A.ImplDecl):
                for method in stmt.methods:
                    target, _ = method.info.extra
                    proto = self.compile_function(f"{target.name}.{method.name}", method.params, method.body, method,
                                                  has_self=method.has_self, top_level=True)
                    fc.at(method.span)
                    fc.emit(Op.CLOSURE, fc.constant(proto))
                    fc.emit(Op.DEFINE_METHOD, fc.constant(method_key(target, method.name)))
        body = [s for s in program.stmts
                if not isinstance(s, (A.FnDecl, A.ImplDecl, A.StructDecl, A.EnumDecl, A.Import, A.TypeAlias))]
        for i, stmt in enumerate(body):
            if return_last and i == len(body) - 1 and isinstance(stmt, A.ExprStmt) and \
                    not self._valueless_if(stmt.expr) and str(prune(stmt.expr.type)) not in ("Nil", "Never"):
                fc.at(stmt.span)
                self.expr(stmt.expr)
                fc.emit(Op.RETURN)
                fc.proto.returns_value = True  # type: ignore[attr-defined]
                return fc.proto
            self.stmt(stmt)
        fc.emit(Op.NIL)
        fc.emit(Op.RETURN)
        return fc.proto

    def compile_function(self, name: str, params: list[A.Param], body: A.Block, node: Any,
                         has_self: bool, top_level: bool = False) -> FunctionProto:
        """Top-level functions and methods only see globals; nested ones and lambdas can capture."""
        arity = len(params) + (1 if has_self else 0)
        fc = FunctionCompiler(name, arity, None if top_level else self.fc, self.module, self.source)
        saved = self.fc
        self.fc = fc
        try:
            fc.depth = 1
            if has_self:
                fc.add_local("self")
            for p in params:
                fc.add_local(p.name)
            fc.at(body.span)
            self.block_value(body, wants_value=True, new_scope=False)
            fc.at(Span(body.span.end - 1, body.span.end))
            fc.emit(Op.RETURN)
        finally:
            self.fc = saved
        return fc.proto

    # -- blocks and statements -----------------------------------------------------------------------

    def block_value(self, block: A.Block, wants_value: bool, new_scope: bool = True) -> None:
        """Compiles a block; with ``wants_value``, leaves its value (the last expression, or nil) on the stack."""
        fc = self.fc
        if new_scope:
            fc.begin_scope()
        stmts = block.stmts
        for i, stmt in enumerate(stmts):
            last = i == len(stmts) - 1
            if last and wants_value and isinstance(stmt, A.ExprStmt) and not self._valueless_if(stmt.expr):
                self.expr(stmt.expr)
                break
            self.stmt(stmt)
        else:
            if wants_value:
                fc.emit(Op.NIL)
        if new_scope:
            fc.end_scope()

    @staticmethod
    def _valueless_if(expr: A.Expr) -> bool:
        return isinstance(expr, A.IfExpr) and expr.else_ is None and (expr.type is None or str(prune(expr.type)) == "Nil")

    def stmt(self, stmt: A.Stmt) -> None:
        fc = self.fc
        fc.at(stmt.span)
        if isinstance(stmt, A.Let):
            self.expr(stmt.value)
            if fc is self.main and fc.depth == 0:      # a top-level let/var is a global
                fc.emit(Op.DEFINE_GLOBAL, fc.constant(stmt.name))
            else:
                fc.emit(Op.SET_LOCAL, fc.add_local(stmt.name))
        elif isinstance(stmt, A.ExprStmt):
            expr = stmt.expr
            if isinstance(expr, A.IfExpr):
                self.if_expr(expr, wants_value=False)
            elif isinstance(expr, A.MatchExpr):
                self.match_expr(expr, wants_value=False)
            else:
                self.expr(expr)
                fc.emit(Op.POP)
        elif isinstance(stmt, A.Assign):
            self.assign(stmt)
        elif isinstance(stmt, A.Return):
            if stmt.value is None:
                fc.emit(Op.NIL)
            else:
                self.expr(stmt.value)
            fc.emit(Op.RETURN)
        elif isinstance(stmt, A.Break):
            loop = fc.loops[-1]
            fc.close_scopes_above(loop.depth)
            loop.breaks.append(fc.emit_jump(Op.JUMP))
        elif isinstance(stmt, A.Continue):
            loop = fc.loops[-1]
            fc.close_scopes_above(loop.depth)
            loop.continues.append(fc.emit_jump(Op.JUMP))
        elif isinstance(stmt, A.While):
            start = fc.here()
            self.expr(stmt.cond)
            exit_jump = fc.emit_jump(Op.JUMP_IF_FALSE)
            loop = Loop(start, fc.depth)
            fc.loops.append(loop)
            self.block_value(stmt.body, wants_value=False)
            fc.loops.pop()
            fc.emit(Op.JUMP, start)
            fc.patch(exit_jump)
            for b in loop.breaks:
                fc.patch(b)
            for c in loop.continues:
                fc.patch(c, start)
        elif isinstance(stmt, A.For):
            self.for_stmt(stmt)
        elif isinstance(stmt, A.FnDecl):
            slot = fc.add_local(stmt.name)   # before the body, so the function can call itself
            proto = self.compile_function(stmt.name, stmt.params, stmt.body, stmt, has_self=False)
            fc.at(stmt.span)
            fc.emit(Op.CLOSURE, fc.constant(proto))
            fc.emit(Op.SET_LOCAL, slot)
        else:  # pragma: no cover - the checker rejects the rest
            raise CompileError(f"can't compile {type(stmt).__name__}")

    def for_stmt(self, stmt: A.For) -> None:
        fc = self.fc
        self.expr(stmt.iterable)
        fc.emit(Op.GET_ITER)
        start = fc.here()
        done = fc.emit_jump(Op.FOR_ITER)
        fc.begin_scope()
        loop = Loop(start, fc.depth - 1, is_for=True)
        fc.loops.append(loop)
        fc.emit(Op.SET_LOCAL, fc.add_local(stmt.var))
        self.block_value(stmt.body, wants_value=False)
        fc.loops.pop()
        continue_at = fc.here()
        fc.end_scope()          # closes the loop variable if a closure captured it
        fc.emit(Op.JUMP, start)
        fc.patch(done)
        for b in loop.breaks:
            fc.patch(b)
        fc.emit(Op.POP)         # the iterator
        for c in loop.continues:
            fc.patch(c, continue_at)

    def assign(self, stmt: A.Assign) -> None:
        fc = self.fc
        target = stmt.target
        op = stmt.op
        if isinstance(target, A.Name):
            if op != "=":
                self.load_name(target)
                self.expr(stmt.value)
                self.arith(op[0], target.type, stmt.span)
            else:
                self.expr(stmt.value)
            self.store_name(target)
        elif isinstance(target, A.Index):
            container, index = fc.temp(), fc.temp()
            self.expr(target.target)
            fc.emit(Op.SET_LOCAL, container)
            self.expr(target.index)
            fc.emit(Op.SET_LOCAL, index)
            fc.emit(Op.GET_LOCAL, container)
            fc.emit(Op.GET_LOCAL, index)
            if op != "=":
                fc.emit(Op.GET_LOCAL, container)
                fc.emit(Op.GET_LOCAL, index)
                fc.emit(Op.INDEX_GET)
                self.expr(stmt.value)
                self.arith(op[0], target.type, stmt.span)
            else:
                self.expr(stmt.value)
            fc.emit(Op.INDEX_SET)
            self._release_temps(2)
        else:
            assert isinstance(target, A.Field) and target.kind == "field"
            obj = fc.temp()
            self.expr(target.target)
            fc.emit(Op.SET_LOCAL, obj)
            fc.emit(Op.GET_LOCAL, obj)
            if op != "=":
                fc.emit(Op.GET_LOCAL, obj)
                fc.emit(Op.GET_FIELD, target.extra)
                self.expr(stmt.value)
                self.arith(op[0], target.type, stmt.span)
            else:
                self.expr(stmt.value)
            fc.emit(Op.SET_FIELD, target.extra)
            self._release_temps(1)

    def _release_temps(self, count: int) -> None:
        fc = self.fc
        for _ in range(count):
            local = fc.locals.pop()
            assert local.name == " temp"
        fc.reset_next_slot()

    # -- names ----------------------------------------------------------------------------------------

    def _resolve(self, name: str, binding: Binding | None) -> tuple[str, int]:
        fc = self.fc
        if binding is None or not binding.global_:
            local = fc.find_local(name)
            if local is not None:
                return "local", local.slot
            up = fc.find_upvalue(name)
            if up is not None:
                return "upvalue", up
        return "global", fc.constant(name)

    def load_name(self, expr: A.Name) -> None:
        kind, index = self._resolve(expr.name, expr.binding)
        self.fc.emit({"local": Op.GET_LOCAL, "upvalue": Op.GET_UPVALUE, "global": Op.GET_GLOBAL}[kind], index)

    def store_name(self, expr: A.Name) -> None:
        kind, index = self._resolve(expr.name, expr.binding)
        self.fc.emit({"local": Op.SET_LOCAL, "upvalue": Op.SET_UPVALUE, "global": Op.SET_GLOBAL}[kind], index)

    # -- expressions ------------------------------------------------------------------------------------

    def expr(self, expr: A.Expr) -> None:
        fc = self.fc
        fc.at(expr.span)
        if isinstance(expr, (A.IntLit, A.FloatLit, A.StrLit)):
            fc.emit(Op.CONST, fc.constant(expr.value))
        elif isinstance(expr, A.BoolLit):
            fc.emit(Op.TRUE if expr.value else Op.FALSE)
        elif isinstance(expr, A.NilLit):
            fc.emit(Op.NIL)
        elif isinstance(expr, A.Template):
            for part in expr.parts:
                if isinstance(part, str):
                    fc.emit(Op.CONST, fc.constant(part))
                else:
                    self.expr(part)
                    if prune(part.type) != STR:
                        fc.emit(Op.TO_STR)
            fc.emit(Op.BUILD_STR, len(expr.parts))
        elif isinstance(expr, A.Name):
            self.load_name(expr)
        elif isinstance(expr, A.ListLit):
            for item in expr.items:
                self.expr(item)
            fc.emit(Op.BUILD_LIST, len(expr.items))
        elif isinstance(expr, A.MapLit):
            for k, v in expr.entries:
                self.expr(k)
                self.expr(v)
            fc.emit(Op.BUILD_MAP, len(expr.entries))
        elif isinstance(expr, A.StructLit):
            self.struct_lit(expr)
        elif isinstance(expr, A.Unary):
            self.expr(expr.operand)
            if expr.op == "not":
                fc.emit(Op.NOT)
            else:
                fc.emit(Op.FNEG if prune(expr.type) == FLOAT else Op.INEG)
        elif isinstance(expr, A.Binary):
            self.binary(expr)
        elif isinstance(expr, A.RangeExpr):
            self.expr(expr.start)
            self.expr(expr.end)
            fc.emit(Op.RANGE, 1 if expr.inclusive else 0)
        elif isinstance(expr, A.Call):
            self.call(expr)
        elif isinstance(expr, A.Index):
            self.expr(expr.target)
            self.expr(expr.index)
            fc.emit(Op.INDEX_GET)
        elif isinstance(expr, A.Field):
            self.field(expr)
        elif isinstance(expr, A.Lambda):
            proto = self.compile_function("<lambda>", expr.params, expr.body, expr, has_self=False)
            fc.emit(Op.CLOSURE, fc.constant(proto))
        elif isinstance(expr, A.IfExpr):
            self.if_expr(expr, wants_value=True)
        elif isinstance(expr, A.MatchExpr):
            self.match_expr(expr, wants_value=True)
        elif isinstance(expr, A.BlockExpr):
            self.block_value(expr.block, wants_value=True)
        else:  # pragma: no cover
            raise CompileError(f"can't compile {type(expr).__name__}")

    def struct_lit(self, expr: A.StructLit) -> None:
        fc = self.fc
        struct = prune(expr.type)
        assert isinstance(struct, StructT)
        # Evaluate fields in the order they're written, then build in declaration order.
        slots: dict[str, int] = {}
        for name, value, _span in expr.fields:
            self.expr(value)
            slot = fc.temp()
            fc.emit(Op.SET_LOCAL, slot)
            slots[name] = slot
        for name in struct.fields:
            fc.emit(Op.GET_LOCAL, slots[name])
        fc.emit(Op.MAKE_STRUCT, fc.constant(runtime_struct(struct)), len(struct.fields))
        self._release_temps(len(slots))

    def arith(self, op: str, t: Any, span: Span) -> None:
        t = prune(t)
        fc = self.fc
        if op == "+":
            if t == STR:
                fc.emit(Op.SADD)
            elif isinstance(t, ListT):
                fc.emit(Op.LADD)
            else:
                fc.emit(Op.FADD if t == FLOAT else Op.IADD)
            return
        table = {"-": (Op.ISUB, Op.FSUB), "*": (Op.IMUL, Op.FMUL), "/": (Op.IDIV, Op.FDIV), "%": (Op.IMOD, Op.FMOD)}
        int_op, float_op = table[op]
        fc.emit(float_op if t == FLOAT else int_op)

    def binary(self, expr: A.Binary) -> None:
        fc = self.fc
        op = expr.op
        if op in ("and", "or"):
            self.expr(expr.left)
            jump = fc.emit_jump(Op.JUMP_IF_FALSE_OR_POP if op == "and" else Op.JUMP_IF_TRUE_OR_POP)
            self.expr(expr.right)
            fc.patch(jump)
            return
        if op == "??":
            self.expr(expr.left)
            jump = fc.emit_jump(Op.JUMP_IF_NOT_NIL)
            self.expr(expr.right)
            fc.patch(jump)
            return
        self.expr(expr.left)
        self.expr(expr.right)
        compare = {"==": Op.EQ, "!=": Op.NE, "<": Op.LT, "<=": Op.LE, ">": Op.GT, ">=": Op.GE}
        if op in compare:
            fc.emit(compare[op])
        else:
            fc.at(expr.span)
            self.arith(op, expr.left.type if op != "+" else expr.type, expr.span)

    def call(self, expr: A.Call) -> None:
        fc = self.fc
        kind = expr.kind
        if kind == "builtin":
            for arg in expr.args:
                self.expr(arg)
            fc.at(expr.span)
            fc.emit(Op.CALL_BUILTIN, fc.constant(expr.callee.name), len(expr.args))  # type: ignore[attr-defined]
        elif kind == "builtin_method":
            self.expr(expr.callee.target)  # type: ignore[attr-defined]
            for arg in expr.args:
                self.expr(arg)
            fc.at(expr.span)
            fc.emit(Op.CALL_METHOD, fc.constant(expr.extra), len(expr.args))
        elif kind == "method":
            target_type, method = expr.extra
            fc.emit(Op.GET_METHOD, fc.constant(method_key(target_type, method.name)))
            self.expr(expr.callee.target)  # type: ignore[attr-defined]
            for arg in expr.args:
                self.expr(arg)
            fc.at(expr.span)
            fc.emit(Op.CALL, len(expr.args) + 1)
        elif kind == "static":
            target_type, method = expr.extra
            fc.emit(Op.GET_METHOD, fc.constant(method_key(target_type, method.name)))
            for arg in expr.args:
                self.expr(arg)
            fc.at(expr.span)
            fc.emit(Op.CALL, len(expr.args))
        elif kind == "variant":
            enum, index = expr.extra
            for arg in expr.args:
                self.expr(arg)
            fc.emit(Op.MAKE_VARIANT, fc.constant(runtime_enum(enum)), index, len(expr.args))
        else:
            self.expr(expr.callee)
            for arg in expr.args:
                self.expr(arg)
            fc.at(expr.span)
            fc.emit(Op.CALL, len(expr.args))

    def field(self, expr: A.Field) -> None:
        fc = self.fc
        kind = expr.kind
        if kind == "field":
            self.expr(expr.target)
            fc.emit(Op.GET_FIELD, expr.extra)
        elif kind == "variant":
            enum, index = expr.extra
            rt = runtime_enum(enum)
            name, arity = rt.variants[index]
            if arity == 0:
                fc.emit(Op.CONST, fc.constant(EnumValue(rt, index, ())))
            else:
                def make(vm: Any, *args: Any, rt: EnumType = rt, index: int = index) -> EnumValue:
                    return EnumValue(rt, index, tuple(args))
                fc.emit(Op.CONST, fc.constant(NativeFn(f"{rt.name}.{name}", make, arity)))
        elif kind == "static":
            target_type, method = expr.extra
            fc.emit(Op.GET_METHOD, fc.constant(method_key(target_type, method.name)))
        elif kind == "module":
            self.expr(expr.target)
            fc.emit(Op.MODULE_GET, fc.constant(expr.name))
        else:  # pragma: no cover
            raise CompileError(f"unresolved field {expr.name}")

    def if_expr(self, expr: A.IfExpr, wants_value: bool) -> None:
        fc = self.fc
        fc.begin_scope()
        if expr.let_name is not None:
            self.expr(expr.cond)
            slot = fc.add_local(expr.let_name)
            fc.emit(Op.DUP)
            fc.emit(Op.SET_LOCAL, slot)
            else_jump = fc.emit_jump(Op.JUMP_IF_NIL)
        else:
            self.expr(expr.cond)
            else_jump = fc.emit_jump(Op.JUMP_IF_FALSE)
        self.block_value(expr.then, wants_value)
        fc.end_scope()
        end_jump = fc.emit_jump(Op.JUMP)
        fc.patch(else_jump)
        if expr.else_ is None:
            if wants_value:
                fc.emit(Op.NIL)
        elif isinstance(expr.else_, A.IfExpr):
            self.if_expr(expr.else_, wants_value)
        else:
            self.block_value(expr.else_, wants_value)
        fc.patch(end_jump)

    def match_expr(self, expr: A.MatchExpr, wants_value: bool) -> None:
        fc = self.fc
        fc.begin_scope()
        self.expr(expr.subject)
        subject = fc.temp()
        fc.emit(Op.SET_LOCAL, subject)
        end_jumps = []
        for arm in expr.arms:
            fc.begin_scope()
            fails: list[int] = []
            fc.at(arm.pattern.span)
            self.pattern(arm.pattern, subject, fails)
            if arm.guard is not None:
                self.expr(arm.guard)
                fails.append(fc.emit_jump(Op.JUMP_IF_FALSE))
            if isinstance(arm.body, A.BlockExpr):
                self.block_value(arm.body.block, wants_value)
            elif wants_value:
                self.expr(arm.body)
            else:
                self.expr(arm.body)
                fc.emit(Op.POP)
            fc.end_scope()
            end_jumps.append(fc.emit_jump(Op.JUMP))
            for f in fails:
                fc.patch(f)
        fc.at(expr.span)
        fc.emit(Op.MATCH_FAIL)
        for j in end_jumps:
            fc.patch(j)
        fc.end_scope()

    def pattern(self, pat: A.Pattern, slot: int, fails: list[int]) -> None:
        """Emits tests for ``pat`` against the value in ``slot``; failing tests jump to ``fails``."""
        fc = self.fc
        if isinstance(pat, A.WildcardPat):
            return
        if isinstance(pat, A.BindPat):
            fc.locals.append(Local(pat.name, slot, fc.depth))   # just another name for the slot
            return
        if isinstance(pat, A.LitPat):
            fc.emit(Op.GET_LOCAL, slot)
            self.expr(pat.value)
            fc.emit(Op.EQ)
            fails.append(fc.emit_jump(Op.JUMP_IF_FALSE))
            return
        if isinstance(pat, A.RangePat):
            if pat.nullable:                       # nil isn't in any range (and can't be compared)
                fc.emit(Op.GET_LOCAL, slot)
                fails.append(fc.emit_jump(Op.JUMP_IF_NIL))
            fc.emit(Op.GET_LOCAL, slot)
            fc.emit(Op.CONST, fc.constant(pat.low))
            fc.emit(Op.GE)
            fails.append(fc.emit_jump(Op.JUMP_IF_FALSE))
            fc.emit(Op.GET_LOCAL, slot)
            fc.emit(Op.CONST, fc.constant(pat.high))
            fc.emit(Op.LE if pat.inclusive else Op.LT)
            fails.append(fc.emit_jump(Op.JUMP_IF_FALSE))
            return
        if isinstance(pat, A.OrPat):
            matched: list[int] = []
            for i, option in enumerate(pat.options):
                if i == len(pat.options) - 1:
                    self.pattern(option, slot, fails)        # the last one fails for real
                    break
                option_fails: list[int] = []
                self.pattern(option, slot, option_fails)
                matched.append(fc.emit_jump(Op.JUMP))
                for f in option_fails:
                    fc.patch(f)                              # try the next alternative
            for m in matched:
                fc.patch(m)
            return
        assert isinstance(pat, A.VariantPat) and pat.resolved is not None
        _enum, index = pat.resolved
        fc.emit(Op.GET_LOCAL, slot)
        fc.emit(Op.TEST_VARIANT, index)
        fails.append(fc.emit_jump(Op.JUMP_IF_FALSE))
        for i, sub in enumerate(pat.args or []):
            if isinstance(sub, A.WildcardPat):
                continue
            inner = fc.temp()
            fc.emit(Op.GET_LOCAL, slot)
            fc.emit(Op.GET_PAYLOAD, i)
            fc.emit(Op.SET_LOCAL, inner)
            self.pattern(sub, inner, fails)


def compile_program(program: A.Program, source: Source, module: str = "main",
                    return_last: bool = False) -> FunctionProto:
    return Compiler(source, module).compile_program(program, return_last)
