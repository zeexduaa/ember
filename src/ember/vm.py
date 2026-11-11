"""The virtual machine: runs bytecode.

Each call gets a :class:`Frame` with its own local slots and operand stack. The
main loop is a single function with the hot state in local variables, because
that's what makes a Python interpreter loop fast enough to be useful.

Built-in methods that take functions (``map``, ``filter``, ``sort_by``...) call
back into ember code through :meth:`VM.call_value`, which runs a nested loop
until that call returns.

Runtime errors (an index out of range, division by zero, ``panic``) become a
:class:`RuntimeFailure` with an ember stack trace: function names and lines.
"""

from __future__ import annotations

import math
import sys
from typing import IO, Any, Callable

from .builtins import BUILTIN_FUNCTIONS, method_impl
from .bytecode import Op
from .values import (Closure, EmberPanic, EnumType, EnumValue, FunctionProto, Module, NativeFn, StructType,
                     StructValue, Upvalue, check_int, format_value, int_div, int_mod)

MAX_FRAMES = 2_000


class RuntimeFailure(Exception):
    """An ember program stopped with an error. ``trace`` is innermost call first."""

    def __init__(self, message: str, trace: list[str]):
        super().__init__(message)
        self.message = message
        self.trace = trace

    def render(self, limit: int = 12) -> str:
        """Repeated frames are collapsed (deep recursion would print thousands), and long traces are cut."""
        lines = [f"runtime error: {self.message}"]
        collapsed: list[str] = []
        i = 0
        while i < len(self.trace):
            j = i
            while j + 1 < len(self.trace) and self.trace[j + 1] == self.trace[i]:
                j += 1
            entry = f"  at {self.trace[i]}"
            if j > i:
                entry += f"  (and {j - i} more times)"
            collapsed.append(entry)
            i = j + 1
        if len(collapsed) > limit:
            hidden = len(collapsed) - limit
            collapsed = collapsed[:limit - 2] + [f"  ... {hidden} more calls ..."] + collapsed[-2:]
        return "\n".join(lines + collapsed)


class Frame:
    __slots__ = ("closure", "proto", "code", "constants", "locals", "stack", "ip", "open_upvalues", "globals")

    def __init__(self, closure: Closure, args: list[Any], globals_: dict[str, Any]):
        proto = closure.proto
        self.closure = closure
        self.proto = proto
        self.code = proto.code
        self.constants = proto.constants
        self.locals: list[Any] = args + [None] * (max(proto.max_locals, len(args)) - len(args))
        self.stack: list[Any] = []
        self.ip = 0
        self.open_upvalues: dict[int, Upvalue] = {}
        self.globals = globals_


class VM:
    def __init__(self, out: IO[str] | None = None, stdin: IO[str] | None = None):
        self.out = out or sys.stdout
        self.stdin = stdin or sys.stdin
        self.frames: list[Frame] = []
        self.methods: dict[tuple[str, str], Closure] = {}
        self.modules: dict[str, Module] = {}
        self.builtins: dict[str, NativeFn] = {
            name: NativeFn(name, spec.impl, None) for name, spec in BUILTIN_FUNCTIONS.items()}
        self.instructions = 0
        self.module_loader: Callable[[str], Module] | None = None

    # -- entry points ---------------------------------------------------------------------------------

    def run_main(self, proto: FunctionProto, globals_: dict[str, Any] | None = None) -> Any:
        globals_ = {} if globals_ is None else globals_
        closure = Closure(proto, [])
        self.frames.append(Frame(closure, [], globals_))
        try:
            return self._run(len(self.frames) - 1)
        except EmberPanic as exc:
            raise RuntimeFailure(exc.message, exc.trace) from None
        except RecursionError:
            raise RuntimeFailure("stack overflow (too much recursion through built-in methods)", []) from None
        finally:
            self.frames.clear()

    def call_value(self, fn: Any, args: list[Any]) -> Any:
        """Calls an ember function from Python (used by map, filter, sort_by...)."""
        if isinstance(fn, NativeFn):
            return fn.fn(self, *args)
        assert isinstance(fn, Closure)
        caller = self.frames[-1] if self.frames else None
        globals_ = self._globals_for(fn.proto.module, caller) if caller is not None else {}
        self._push_frame(fn, list(args), globals_)
        return self._run(len(self.frames) - 1)

    def _push_frame(self, closure: Closure, args: list[Any], globals_: dict[str, Any]) -> Frame:
        if len(self.frames) >= MAX_FRAMES:
            raise EmberPanic(f"stack overflow: more than {MAX_FRAMES} calls deep (runaway recursion?)")
        frame = Frame(closure, args, globals_)
        self.frames.append(frame)
        return frame

    def _trace(self) -> list[str]:
        trace = []
        for frame in reversed(self.frames):
            proto = frame.proto
            ip = max(0, min(frame.ip - 1, len(proto.lines) - 1))
            line = proto.lines[ip] if proto.lines else 0
            trace.append(f"{proto.name} ({proto.source_name}:{line})")
        return trace

    # -- the loop ---------------------------------------------------------------------------------------

    def _run(self, base: int) -> Any:
        """Runs until the frame at index ``base`` returns; returns its result."""
        try:
            return self._loop(base)
        except EmberPanic as exc:
            if not exc.trace:
                exc.trace = self._trace()
            del self.frames[base:]
            raise
        except ZeroDivisionError:
            exc = EmberPanic("division by zero")
            exc.trace = self._trace()
            del self.frames[base:]
            raise exc from None

    def _loop(self, base: int) -> Any:  # noqa: C901 - one big dispatch loop is the point
        frames = self.frames
        frame = frames[-1]
        code, constants, locals_, stack = frame.code, frame.constants, frame.locals, frame.stack
        push, pop = stack.append, stack.pop
        O = Op
        CONST, NIL, TRUE, FALSE, POP, DUP = O.CONST, O.NIL, O.TRUE, O.FALSE, O.POP, O.DUP
        GET_LOCAL, SET_LOCAL, GET_UPVALUE, SET_UPVALUE = O.GET_LOCAL, O.SET_LOCAL, O.GET_UPVALUE, O.SET_UPVALUE
        GET_GLOBAL, IADD, ISUB, IMUL, LT, LE, GT, GE, EQ = O.GET_GLOBAL, O.IADD, O.ISUB, O.IMUL, O.LT, O.LE, O.GT, O.GE, O.EQ
        JUMP, JUMP_IF_FALSE, CALL, RETURN, FOR_ITER = O.JUMP, O.JUMP_IF_FALSE, O.CALL, O.RETURN, O.FOR_ITER
        INT_MIN, INT_MAX = -(2 ** 63), 2 ** 63 - 1
        ip = frame.ip
        while True:
            op = code[ip]
            # The most common instructions first.
            if op == GET_LOCAL:
                push(locals_[code[ip + 1]])
                ip += 2
            elif op == CONST:
                push(constants[code[ip + 1]])
                ip += 2
            elif op == SET_LOCAL:
                locals_[code[ip + 1]] = pop()
                ip += 2
            elif op == IADD:
                b = pop()
                r = pop() + b
                if not INT_MIN <= r <= INT_MAX:
                    frame.ip = ip + 1
                    raise EmberPanic("integer overflow (Int is 64-bit)")
                push(r)
                ip += 1
            elif op == ISUB:
                b = pop()
                r = pop() - b
                if not INT_MIN <= r <= INT_MAX:
                    frame.ip = ip + 1
                    raise EmberPanic("integer overflow (Int is 64-bit)")
                push(r)
                ip += 1
            elif op == LT:
                b = pop()
                push(pop() < b)
                ip += 1
            elif op == LE:
                b = pop()
                push(pop() <= b)
                ip += 1
            elif op == GT:
                b = pop()
                push(pop() > b)
                ip += 1
            elif op == GE:
                b = pop()
                push(pop() >= b)
                ip += 1
            elif op == EQ:
                b = pop()
                push(_equal(pop(), b))
                ip += 1
            elif op == JUMP:
                ip = code[ip + 1]
            elif op == JUMP_IF_FALSE:
                ip = ip + 2 if pop() else code[ip + 1]
            elif op == GET_GLOBAL:
                name = constants[code[ip + 1]]
                try:
                    push(frame.globals[name])
                except KeyError:
                    builtin = self.builtins.get(name)
                    if builtin is None:
                        frame.ip = ip + 2
                        raise EmberPanic(f"'{name}' is used before it has a value") from None
                    push(builtin)
                ip += 2
            elif op == GET_UPVALUE:
                push(frame.closure.upvalues[code[ip + 1]].get())
                ip += 2
            elif op == CALL:
                argc = code[ip + 1]
                frame.ip = ip + 2
                args = stack[len(stack) - argc:] if argc else []
                del stack[len(stack) - argc:]
                callee = pop()
                if isinstance(callee, Closure):
                    frame = self._push_frame(callee, args, frame.globals if callee.proto.module == frame.proto.module
                                             else self._globals_for(callee.proto.module, frame))
                    code, constants, locals_, stack = frame.code, frame.constants, frame.locals, frame.stack
                    push, pop = stack.append, stack.pop
                    ip = 0
                elif isinstance(callee, NativeFn):
                    push(callee.fn(self, *args))
                    ip = frame.ip
                else:  # pragma: no cover - the checker prevents this
                    raise EmberPanic(f"can't call {format_value(callee)}")
            elif op == RETURN:
                result = pop()
                for up in frame.open_upvalues.values():
                    up.close()
                frames.pop()
                if len(frames) <= base:
                    return result
                frame = frames[-1]
                code, constants, locals_, stack = frame.code, frame.constants, frame.locals, frame.stack
                push, pop = stack.append, stack.pop
                push(result)
                ip = frame.ip
            elif op == FOR_ITER:
                try:
                    push(next(stack[-1]))
                    ip += 2
                except StopIteration:
                    ip = code[ip + 1]
            elif op == IMUL:
                b = pop()
                r = pop() * b
                if not INT_MIN <= r <= INT_MAX:
                    frame.ip = ip + 1
                    raise EmberPanic("integer overflow (Int is 64-bit)")
                push(r)
                ip += 1
            elif op == POP:
                pop()
                ip += 1
            elif op == NIL:
                push(None)
                ip += 1
            elif op == TRUE:
                push(True)
                ip += 1
            elif op == FALSE:
                push(False)
                ip += 1
            elif op == DUP:
                push(stack[-1])
                ip += 1
            elif op == SET_UPVALUE:
                frame.closure.upvalues[code[ip + 1]].set(pop())
                ip += 2
            else:
                frame.ip = ip
                ip = self._slow(op, frame, code, constants, stack)

    def _globals_for(self, module: str, frame: Frame | None) -> dict[str, Any]:
        """Each module's functions run with that module's globals."""
        found = self.modules.get(module)
        if found is not None:
            return found.globals
        return frame.globals if frame is not None else {}

    def _slow(self, op: int, frame: Frame, code: list[int], constants: list[Any], stack: list[Any]) -> int:
        """The less common instructions. Returns the next ip."""
        ip = frame.ip
        pop, push = stack.pop, stack.append
        O = Op
        if op == O.SET_GLOBAL or op == O.DEFINE_GLOBAL:
            frame.globals[constants[code[ip + 1]]] = pop()
            return ip + 2
        if op == O.CLOSE_UPVALUE:
            up = frame.open_upvalues.pop(code[ip + 1], None)
            if up is not None:
                up.close()
            return ip + 2
        if op in (O.IDIV, O.IMOD):
            b = pop()
            a = pop()
            frame.ip = ip + 1
            push(int_div(a, b) if op == O.IDIV else int_mod(a, b))
            return ip + 1
        if op == O.INEG:
            frame.ip = ip + 1
            push(check_int(-pop()))
            return ip + 1
        if op in (O.FADD, O.FSUB, O.FMUL, O.FDIV, O.FMOD):
            b = pop()
            a = pop()
            frame.ip = ip + 1
            if op == O.FADD:
                push(a + b)
            elif op == O.FSUB:
                push(a - b)
            elif op == O.FMUL:
                push(a * b)
            elif op == O.FDIV:
                if b == 0:
                    raise EmberPanic("division by zero")
                push(a / b)
            else:
                if b == 0:
                    raise EmberPanic("division by zero (in %)")
                push(math.fmod(a, b))
            return ip + 1
        if op == O.FNEG:
            push(-pop())
            return ip + 1
        if op == O.SADD:
            b = pop()
            push(pop() + b)
            return ip + 1
        if op == O.LADD:
            b = pop()
            push(pop() + b)
            return ip + 1
        if op == O.NOT:
            push(not pop())
            return ip + 1
        if op == O.NE:
            b = pop()
            push(not _equal(pop(), b))
            return ip + 1
        if op == O.JUMP_IF_FALSE_OR_POP:
            if not stack[-1]:
                return code[ip + 1]
            pop()
            return ip + 2
        if op == O.JUMP_IF_TRUE_OR_POP:
            if stack[-1]:
                return code[ip + 1]
            pop()
            return ip + 2
        if op == O.JUMP_IF_NOT_NIL:
            if stack[-1] is not None:
                return code[ip + 1]
            pop()
            return ip + 2
        if op == O.JUMP_IF_NIL:
            return code[ip + 1] if pop() is None else ip + 2
        if op == O.CALL_BUILTIN:
            name = constants[code[ip + 1]]
            argc = code[ip + 2]
            args = stack[len(stack) - argc:] if argc else []
            del stack[len(stack) - argc:]
            frame.ip = ip + 3
            push(self.builtins[name].fn(self, *args))
            return ip + 3
        if op == O.CALL_METHOD:
            name = constants[code[ip + 1]]
            argc = code[ip + 2]
            args = stack[len(stack) - argc:] if argc else []
            del stack[len(stack) - argc:]
            receiver = pop()
            frame.ip = ip + 3
            impl = method_impl(receiver, name)
            if impl is None:  # pragma: no cover - the checker prevents this
                raise EmberPanic(f"{format_value(receiver, True)} has no method {name}")
            push(impl(self, receiver, *args))
            return ip + 3
        if op == O.CLOSURE:
            proto: FunctionProto = constants[code[ip + 1]]
            upvalues = []
            for is_local, index in proto.upvalues:
                if is_local:
                    up = frame.open_upvalues.get(index)
                    if up is None:
                        up = Upvalue(frame.locals, index)
                        frame.open_upvalues[index] = up
                    upvalues.append(up)
                else:
                    upvalues.append(frame.closure.upvalues[index])
            push(Closure(proto, upvalues))
            return ip + 2
        if op == O.BUILD_LIST:
            n = code[ip + 1]
            items = stack[len(stack) - n:] if n else []
            del stack[len(stack) - n:]
            push(items)
            return ip + 2
        if op == O.BUILD_MAP:
            n = code[ip + 1]
            flat = stack[len(stack) - 2 * n:] if n else []
            del stack[len(stack) - 2 * n:]
            push({flat[i]: flat[i + 1] for i in range(0, len(flat), 2)})
            return ip + 2
        if op == O.BUILD_STR:
            n = code[ip + 1]
            parts = stack[len(stack) - n:]
            del stack[len(stack) - n:]
            push("".join(parts))
            return ip + 2
        if op == O.TO_STR:
            push(format_value(pop()))
            return ip + 1
        if op == O.RANGE:
            end = pop()
            start = pop()
            push(range(start, end + 1 if code[ip + 1] else end))
            return ip + 2
        if op == O.INDEX_GET:
            index = pop()
            container = pop()
            frame.ip = ip + 1
            push(_index_get(container, index))
            return ip + 1
        if op == O.INDEX_SET:
            value = pop()
            index = pop()
            container = pop()
            frame.ip = ip + 1
            if isinstance(container, list):
                if not -len(container) <= index < len(container):
                    raise EmberPanic(f"index {index} is out of range for a list of length {len(container)}")
            container[index] = value
            return ip + 1
        if op == O.GET_FIELD:
            push(pop().values[code[ip + 1]])
            return ip + 2
        if op == O.SET_FIELD:
            value = pop()
            pop().values[code[ip + 1]] = value
            return ip + 2
        if op == O.MAKE_STRUCT:
            struct_type: StructType = constants[code[ip + 1]]
            n = code[ip + 2]
            values = stack[len(stack) - n:] if n else []
            del stack[len(stack) - n:]
            push(StructValue(struct_type, values))
            return ip + 3
        if op == O.MAKE_VARIANT:
            enum_type: EnumType = constants[code[ip + 1]]
            index, n = code[ip + 2], code[ip + 3]
            values = tuple(stack[len(stack) - n:]) if n else ()
            del stack[len(stack) - n:]
            push(EnumValue(enum_type, index, values))
            return ip + 4
        if op == O.TEST_VARIANT:
            value = pop()
            push(isinstance(value, EnumValue) and value.index == code[ip + 1])
            return ip + 2
        if op == O.GET_PAYLOAD:
            push(pop().values[code[ip + 1]])
            return ip + 2
        if op == O.GET_ITER:
            iterable = pop()
            if isinstance(iterable, (list, dict)):
                iterable = list(iterable)   # a snapshot: changing the list inside the loop is safe
            push(iter(iterable))
            return ip + 1
        if op == O.GET_METHOD:
            key = constants[code[ip + 1]]
            method = self.methods.get(key)
            if method is None:
                frame.ip = ip + 2
                raise EmberPanic(f"method {key[1]} isn't defined yet")
            push(method)
            return ip + 2
        if op == O.DEFINE_METHOD:
            self.methods[constants[code[ip + 1]]] = pop()
            return ip + 2
        if op == O.MODULE_GET:
            module = pop()
            push(module.globals[constants[code[ip + 1]]])
            return ip + 2
        if op == O.MATCH_FAIL:
            frame.ip = ip + 1
            raise EmberPanic("no match arm matched (this is a bug in ember's exhaustiveness check)")
        raise EmberPanic(f"unknown instruction {op}")  # pragma: no cover


def _equal(a: Any, b: Any) -> bool:
    # True == 1 in Python, but the checker never lets a Bool meet an Int, so == is safe as is.
    return a == b


def _index_get(container: Any, index: Any) -> Any:
    if isinstance(container, list):
        if not -len(container) <= index < len(container):
            raise EmberPanic(f"index {index} is out of range for a list of length {len(container)}")
        return container[index]
    if isinstance(container, str):
        if not -len(container) <= index < len(container):
            raise EmberPanic(f"index {index} is out of range for a string of length {len(container)}")
        return container[index]
    try:
        return container[index]
    except KeyError:
        raise EmberPanic(f"key {format_value(index, True)} isn't in the map "
                         "(use .get(key) for an optional value)") from None
