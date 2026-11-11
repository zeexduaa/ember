"""Runtime values.

ember values map onto Python ones where that's exact: Int -> int (kept to 64
bits by the VM), Float -> float, Bool -> bool, Str -> str, Nil -> None,
List -> list, Map -> dict (insertion-ordered). Structs, enum values and
functions get small classes here.

An optional ``T?`` has no wrapper: it's either nil (None) or the value itself.
The checker makes sure ``T??`` can't happen, so that's unambiguous.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

INT_MIN, INT_MAX = -(2 ** 63), 2 ** 63 - 1


class EmberPanic(Exception):
    """A runtime error inside an ember program. The VM adds the ember stack trace."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message
        self.trace: list[str] = []


@dataclass(eq=False)
class FunctionProto:
    """Compiled code for one function."""
    name: str
    arity: int
    code: list[Any] = field(default_factory=list)
    lines: list[int] = field(default_factory=list)       # source line for each instruction
    constants: list[Any] = field(default_factory=list)
    upvalues: list[tuple[bool, int]] = field(default_factory=list)   # (is_local, index) per captured variable
    max_locals: int = 0
    module: str = "main"
    source_name: str = "<input>"

    def __repr__(self) -> str:
        return f"<fn {self.name}>"


class Upvalue:
    """A captured variable. While open it points at a stack slot; closing copies the value out."""
    __slots__ = ("stack", "index", "value", "closed")

    def __init__(self, stack: list[Any], index: int):
        self.stack = stack
        self.index = index
        self.value: Any = None
        self.closed = False

    def get(self) -> Any:
        return self.value if self.closed else self.stack[self.index]

    def set(self, value: Any) -> None:
        if self.closed:
            self.value = value
        else:
            self.stack[self.index] = value

    def close(self) -> None:
        self.value = self.stack[self.index]
        self.closed = True


class Closure:
    __slots__ = ("proto", "upvalues")

    def __init__(self, proto: FunctionProto, upvalues: list[Upvalue]):
        self.proto = proto
        self.upvalues = upvalues

    def __repr__(self) -> str:
        return f"<fn {self.proto.name}>"


@dataclass(eq=False)
class NativeFn:
    name: str
    fn: Callable[..., Any]
    arity: int | None          # None: any number of arguments

    def __repr__(self) -> str:
        return f"<builtin {self.name}>"


@dataclass(eq=False)
class StructType:
    name: str
    fields: list[str]


class StructValue:
    __slots__ = ("type", "values")

    def __init__(self, type_: StructType, values: list[Any]):
        self.type = type_
        self.values = values

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, StructValue) or other.type is not self.type:
            return False
        if other is self:
            return True
        # Structs can refer to themselves (a node in its own list). While a pair is being
        # compared, meeting it again counts as equal, so comparing two such cycles ends.
        pair = (id(self), id(other))
        seen = _comparing()
        if pair in seen:
            return True
        seen.add(pair)
        try:
            return other.values == self.values
        finally:
            seen.discard(pair)

    def __hash__(self) -> int:  # pragma: no cover - structs aren't map keys
        return id(self)


_state = threading.local()


def _comparing() -> set[tuple[int, int]]:
    seen = getattr(_state, "comparing", None)
    if seen is None:
        seen = _state.comparing = set()
    return seen


@dataclass(eq=False)
class EnumType:
    name: str
    variants: list[tuple[str, int]]        # (name, number of fields)


class EnumValue:
    __slots__ = ("type", "index", "values")

    def __init__(self, type_: EnumType, index: int, values: tuple[Any, ...]):
        self.type = type_
        self.index = index
        self.values = values

    @property
    def variant(self) -> str:
        return self.type.variants[self.index][0]

    def __eq__(self, other: object) -> bool:
        return (isinstance(other, EnumValue) and other.type is self.type and other.index == self.index
                and other.values == self.values)

    def __hash__(self) -> int:  # pragma: no cover
        return hash((id(self.type), self.index))


@dataclass(eq=False)
class Module:
    name: str
    globals: dict[str, Any]


def format_value(value: Any, quoted: bool = False, _seen: set[int] | None = None) -> str:
    """How print() and str() show a value. Strings are quoted only inside collections.
    A struct met again inside itself is shown as ``...``."""
    if value is None:
        return "nil"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, float):
        return format_float(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return quote(value) if quoted else value
    if isinstance(value, (list, dict, StructValue, EnumValue)):
        seen = set() if _seen is None else _seen
        if id(value) in seen:
            return "..."
        seen.add(id(value))
        try:
            return _format_container(value, seen)
        finally:
            seen.discard(id(value))
    if isinstance(value, Closure):
        return f"<fn {value.proto.name}>"
    if isinstance(value, NativeFn):
        return f"<builtin {value.name}>"
    if isinstance(value, range):
        return f"{value.start}..{value.stop}"
    return repr(value)  # pragma: no cover


def _format_container(value: Any, seen: set[int]) -> str:
    def show(v: Any) -> str:
        return format_value(v, True, seen)
    if isinstance(value, list):
        return "[" + ", ".join(show(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{show(k)}: {show(v)}" for k, v in value.items()) + "}"
    if isinstance(value, StructValue):
        inner = ", ".join(f"{name}: {show(v)}" for name, v in zip(value.type.fields, value.values))
        return f"{value.type.name} {{ {inner} }}" if inner else f"{value.type.name} {{}}"
    name = f"{value.type.name}.{value.variant}"
    if value.values:
        return f"{name}({', '.join(show(v) for v in value.values)})"
    return name


def format_float(value: float) -> str:
    if math.isnan(value):
        return "nan"
    if math.isinf(value):
        return "inf" if value > 0 else "-inf"
    if value == 0:
        return "-0.0" if math.copysign(1.0, value) < 0 else "0.0"
    if value == int(value) and abs(value) < 1e16:
        return f"{int(value)}.0"
    return repr(value)


def quote(s: str) -> str:
    out = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t").replace("\r", "\\r")
    return f'"{out}"'


def check_int(value: int) -> int:
    if not INT_MIN <= value <= INT_MAX:
        raise EmberPanic("integer overflow (Int is 64-bit)")
    return value


def int_div(a: int, b: int) -> int:
    """Division that rounds toward zero, like C, Go and Rust (Python's // rounds down)."""
    if b == 0:
        raise EmberPanic("division by zero")
    q = abs(a) // abs(b)
    return check_int(q if (a >= 0) == (b >= 0) else -q)


def int_mod(a: int, b: int) -> int:
    """The remainder that goes with int_div: it has the sign of a."""
    if b == 0:
        raise EmberPanic("division by zero (in %)")
    return a - b * int_div(a, b)
