"""Built-in functions and methods: their types (for the checker) and their code (for the VM).

Keeping both in one place means a built-in can't be documented, checked and run
three different ways. Each method's implementation gets the VM first, so methods
like ``map`` can call back into ember code.
"""

from __future__ import annotations

import math
import sys
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from . import ast as A
from .types import (BOOL, FLOAT, INT, NEVER, NIL, STR, FnT, ListT, MapT, OptionT, Type, TypeVar, is_comparable,
                    is_numeric, prune, resolve)
from .values import EmberPanic, check_int, format_value

if TYPE_CHECKING:
    from .checker import Checker


# -- functions --------------------------------------------------------------------------------------


@dataclass
class FunctionSpec:
    name: str
    type: FnT | None                  # None for functions with special checking (print, str, min, max, assert)
    impl: Callable[..., Any]          # impl(vm, *args)
    doc: str
    special: bool = False             # can't be used as a value (no single type)

    def check(self, checker: "Checker", call: A.Call, expected: Type | None) -> Type:
        if self.special:
            return SPECIAL_CHECKS[self.name](checker, call)
        assert self.type is not None
        return checker.check_args(call, self.type, f"'{self.name}'")


def _print(vm: Any, *args: Any) -> None:
    vm.out.write(" ".join(format_value(a) for a in args) + "\n")


def _input(vm: Any, prompt: str) -> str | None:
    vm.out.write(prompt)
    vm.out.flush()
    line = vm.stdin.readline()
    if line == "":
        return None
    return line.rstrip("\n").rstrip("\r")


def _int(vm: Any, x: float) -> int:
    if math.isnan(x) or math.isinf(x):
        raise EmberPanic(f"can't convert {format_value(x)} to an Int")
    return check_int(int(x))


def _parse_int(vm: Any, s: str) -> int | None:
    try:
        value = int(s.strip().replace("_", ""), 0) if s.strip().lower().startswith(("0x", "0b", "0o")) else int(s.strip())
    except ValueError:
        return None
    return value if -(2 ** 63) <= value < 2 ** 63 else None


def _parse_float(vm: Any, s: str) -> float | None:
    try:
        return float(s.strip())
    except ValueError:
        return None


def _sqrt(vm: Any, x: float) -> float:
    if x < 0:
        raise EmberPanic(f"sqrt of a negative number ({format_value(x)})")
    return math.sqrt(x)


def _round(vm: Any, x: float) -> int:
    if math.isnan(x) or math.isinf(x):
        raise EmberPanic(f"can't round {format_value(x)}")
    return check_int(math.floor(x + 0.5))   # halves round up, unlike Python's banker's rounding


def _pow(vm: Any, a: float, b: float) -> float:
    try:
        return math.pow(a, b)
    except (OverflowError, ValueError):
        raise EmberPanic(f"pow({format_value(a)}, {format_value(b)}) has no Float result") from None


def _panic(vm: Any, message: str) -> None:
    raise EmberPanic(message)


def _assert(vm: Any, cond: bool, message: str = "assertion failed") -> None:
    if not cond:
        raise EmberPanic(message)


def _min(vm: Any, a: Any, b: Any) -> Any:
    return a if a <= b else b


def _max(vm: Any, a: Any, b: Any) -> Any:
    return a if a >= b else b


def _check_print(checker: "Checker", call: A.Call) -> Type:
    for arg in call.args:
        t = prune(checker.check_expr(arg))
        if isinstance(t, FnT):
            checker.error("a function can't be printed", arg.span)
    return NIL


def _check_str(checker: "Checker", call: A.Call) -> Type:
    if len(call.args) != 1:
        raise checker.fail(f"'str' takes 1 argument, but {len(call.args)} were given", call.span)
    t = prune(checker.check_expr(call.args[0]))
    if isinstance(t, FnT):
        checker.error("a function can't be turned into a string", call.args[0].span)
    return STR


def _check_min_max(checker: "Checker", call: A.Call) -> Type:
    name = call.callee.name  # type: ignore[attr-defined]
    if len(call.args) != 2:
        raise checker.fail(f"'{name}' takes 2 arguments, but {len(call.args)} were given", call.span,
                           notes=["for a list, use .min() or .max()"] if len(call.args) == 1 else [])
    a = checker.check_expr(call.args[0])
    b = checker.check_expr(call.args[1], a)
    checker.expect(b, a, call.args[1].span, f"'{name}'")
    if not is_comparable(a) and not isinstance(prune(a), TypeVar):
        checker.error(f"'{name}' works on Int, Float or Str, not {resolve(a)}", call.args[0].span)
    return a


def _check_assert(checker: "Checker", call: A.Call) -> Type:
    if len(call.args) not in (1, 2):
        raise checker.fail("'assert' takes a condition and an optional message", call.span)
    checker.expect(checker.check_expr(call.args[0], BOOL), BOOL, call.args[0].span, "'assert'")
    if len(call.args) == 2:
        checker.expect(checker.check_expr(call.args[1], STR), STR, call.args[1].span, "'assert' message")
    return NIL


SPECIAL_CHECKS = {"print": _check_print, "str": _check_str, "min": _check_min_max, "max": _check_min_max,
                  "assert": _check_assert}


def _fn(*params: Type, ret: Type) -> FnT:
    return FnT(tuple(params), ret)


BUILTIN_FUNCTIONS: dict[str, FunctionSpec] = {s.name: s for s in [
    FunctionSpec("print", None, _print, "print(values...): writes values separated by spaces, then a newline",
                 special=True),
    FunctionSpec("str", None, lambda vm, x: format_value(x), "str(value) -> Str: how print shows the value",
                 special=True),
    FunctionSpec("min", None, _min, "min(a, b): the smaller of two Ints, Floats or Strs", special=True),
    FunctionSpec("max", None, _max, "max(a, b): the larger of two Ints, Floats or Strs", special=True),
    FunctionSpec("assert", None, _assert, "assert(cond, message?): stops the program if cond is false",
                 special=True),
    FunctionSpec("input", _fn(STR, ret=OptionT(STR)), _input, "input(prompt) -> Str?: a line from the keyboard, nil at end of input"),
    FunctionSpec("int", _fn(FLOAT, ret=INT), _int, "int(x: Float) -> Int: drops the fraction (toward zero)"),
    FunctionSpec("float", _fn(INT, ret=FLOAT), lambda vm, x: float(x), "float(x: Int) -> Float"),
    FunctionSpec("parse_int", _fn(STR, ret=OptionT(INT)), _parse_int, "parse_int(s) -> Int?: nil if s isn't a whole number"),
    FunctionSpec("parse_float", _fn(STR, ret=OptionT(FLOAT)), _parse_float, "parse_float(s) -> Float?: nil if s isn't a number"),
    FunctionSpec("sqrt", _fn(FLOAT, ret=FLOAT), _sqrt, "sqrt(x: Float) -> Float"),
    FunctionSpec("pow", _fn(FLOAT, FLOAT, ret=FLOAT), _pow, "pow(a: Float, b: Float) -> Float"),
    FunctionSpec("floor", _fn(FLOAT, ret=INT), lambda vm, x: _int(vm, math.floor(x)) if math.isfinite(x) else _int(vm, x),
                 "floor(x: Float) -> Int"),
    FunctionSpec("ceil", _fn(FLOAT, ret=INT), lambda vm, x: _int(vm, math.ceil(x)) if math.isfinite(x) else _int(vm, x),
                 "ceil(x: Float) -> Int"),
    FunctionSpec("round", _fn(FLOAT, ret=INT), _round, "round(x: Float) -> Int: halves round up (2.5 -> 3)"),
    FunctionSpec("clock", _fn(ret=FLOAT), lambda vm: time.perf_counter(), "clock() -> Float: seconds, for timing"),
    FunctionSpec("panic", _fn(STR, ret=NEVER), _panic, "panic(message): stops the program with an error"),
]}


# -- methods --------------------------------------------------------------------------------------------


@dataclass
class MethodSig:
    type: FnT                                       # without the receiver
    after: Callable[[Type], str | None] | None = None   # extra check once argument types are known


Impl = Callable[..., Any]


def _list_index(xs: list, i: int) -> int:
    if not -len(xs) <= i < len(xs):
        raise EmberPanic(f"index {i} is out of range for a list of length {len(xs)}")
    return i


def _sorted_key(vm: Any, xs: list, key: Any = None) -> list:
    if key is None:
        return sorted(xs)
    keyed = [(vm.call_value(key, [x]), i, x) for i, x in enumerate(xs)]
    keyed.sort(key=lambda t: (t[0], t[1]))
    return [x for _, _, x in keyed]


def _sum(vm: Any, xs: list) -> Any:
    total: Any = 0 if not xs or isinstance(xs[0], int) else 0.0
    for x in xs:
        total += x
    return check_int(total) if isinstance(total, int) else total


def _slice(n: int, start: int, end: int) -> tuple[int, int]:
    if start < 0:
        start += n
    if end < 0:
        end += n
    if not 0 <= start <= n or not 0 <= end <= n or start > end:
        raise EmberPanic(f"slice({start}, {end}) is out of range for length {n}")
    return start, end


def _list_remove(vm: Any, xs: list, i: int) -> Any:
    return xs.pop(_list_index(xs, i))


def _list_insert(vm: Any, xs: list, i: int, x: Any) -> None:
    if not 0 <= i <= len(xs):
        raise EmberPanic(f"insert position {i} is out of range for a list of length {len(xs)}")
    xs.insert(i, x)


def _min_max_list(name: str, pick: Callable[..., Any]) -> Impl:
    def impl(vm: Any, xs: list) -> Any:
        return pick(xs) if xs else None
    return impl


def _str_index_of(vm: Any, s: str, sub: str) -> int | None:
    i = s.find(sub)
    return None if i < 0 else i


def _repeat(vm: Any, s: str, n: int) -> str:
    if n < 0:
        raise EmberPanic(f"can't repeat a string {n} times")
    if len(s) * n > 100_000_000:
        raise EmberPanic("the repeated string would be too long")
    return s * n


def _split(vm: Any, s: str, sep: str) -> list[str]:
    if sep == "":
        return list(s)
    return s.split(sep)


def _int_pow(vm: Any, a: int, b: int) -> int:
    if b < 0:
        raise EmberPanic("negative exponent: use pow() with Floats")
    if b > 64 and abs(a) > 1:
        raise EmberPanic("integer overflow (Int is 64-bit)")
    return check_int(a ** b)


INT_BITS = 64
_INT_MIN, _INT_MAX = -(2 ** 63), 2 ** 63 - 1
_EXACT = {"add": lambda a, b: a + b, "sub": lambda a, b: a - b, "mul": lambda a, b: a * b}


def _wrap(n: int) -> int:
    """Two's complement wrap-around to 64 bits, as C, Go and Rust's wrapping_* do."""
    n &= (1 << INT_BITS) - 1
    return n - (1 << INT_BITS) if n > _INT_MAX else n


def _checked(op: str) -> Impl:
    def impl(vm: Any, a: int, b: int) -> int | None:
        if op == "div":
            if b == 0 or (a == _INT_MIN and b == -1):
                return None
            q = abs(a) // abs(b)
            return q if (a >= 0) == (b >= 0) else -q
        r = _EXACT[op](a, b)
        return r if _INT_MIN <= r <= _INT_MAX else None
    return impl


def _wrapping(op: str) -> Impl:
    return lambda vm, a, b: _wrap(_EXACT[op](a, b))


def _saturating(op: str) -> Impl:
    return lambda vm, a, b: max(_INT_MIN, min(_INT_MAX, _EXACT[op](a, b)))


def _map_get_or(vm: Any, m: dict, k: Any, default: Any) -> Any:
    return m.get(k, default)


def builtin_method(receiver: Type, name: str) -> MethodSig | None:
    t = prune(receiver)
    table = _method_table(t)
    entry = table.get(name) if table else None
    if entry is None:
        return None
    return entry[0]()


def method_names(receiver: Type) -> list[str]:
    table = _method_table(prune(receiver))
    return sorted(table) if table else []


def method_impl(value: Any, name: str) -> Impl | None:
    """Finds a method's code from the runtime value."""
    if isinstance(value, list):
        kind = "list"
    elif isinstance(value, str):
        kind = "str"
    elif isinstance(value, dict):
        kind = "map"
    elif isinstance(value, bool):
        return None
    elif isinstance(value, int):
        kind = "int"
    elif isinstance(value, float):
        kind = "float"
    else:
        return None
    entry = IMPLS[kind].get(name)
    return entry


def _method_table(t: Type) -> dict[str, tuple[Callable[[], MethodSig], Impl]] | None:
    if isinstance(t, ListT):
        return _list_methods(t.elem)
    if t == STR:
        return _str_methods()
    if isinstance(t, MapT):
        return _map_methods(t.key, t.value)
    if t == INT:
        return _int_methods()
    if t == FLOAT:
        return _float_methods()
    return None


def _needs(kind: str, check: Callable[[Type], bool]) -> Callable[[Type], str | None]:
    def after(receiver: Type) -> str | None:
        elem = prune(receiver.elem) if isinstance(prune(receiver), ListT) else prune(receiver)  # type: ignore[attr-defined]
        if isinstance(elem, TypeVar) or check(elem):
            return None
        return f"this needs a list of {kind}, not {resolve(receiver)}"
    return after


def _list_methods(T: Type) -> dict[str, tuple[Callable[[], MethodSig], Impl]]:
    LT = ListT(T)

    def sig(*params: Type, ret: Type, after: Callable[[Type], str | None] | None = None) -> Callable[[], MethodSig]:
        return lambda: MethodSig(FnT(tuple(params), ret), after)

    def generic(build: Callable[[TypeVar], MethodSig]) -> Callable[[], MethodSig]:
        return lambda: build(TypeVar())

    return {
        "len": (sig(ret=INT), IMPLS["list"]["len"]),
        "is_empty": (sig(ret=BOOL), IMPLS["list"]["is_empty"]),
        "push": (sig(T, ret=NIL), IMPLS["list"]["push"]),
        "pop": (sig(ret=OptionT(T)), IMPLS["list"]["pop"]),
        "insert": (sig(INT, T, ret=NIL), IMPLS["list"]["insert"]),
        "remove": (sig(INT, ret=T), IMPLS["list"]["remove"]),
        "extend": (sig(LT, ret=NIL), IMPLS["list"]["extend"]),
        "clear": (sig(ret=NIL), IMPLS["list"]["clear"]),
        "contains": (sig(T, ret=BOOL), IMPLS["list"]["contains"]),
        "index_of": (sig(T, ret=OptionT(INT)), IMPLS["list"]["index_of"]),
        "first": (sig(ret=OptionT(T)), IMPLS["list"]["first"]),
        "last": (sig(ret=OptionT(T)), IMPLS["list"]["last"]),
        "slice": (sig(INT, INT, ret=LT), IMPLS["list"]["slice"]),
        "copy": (sig(ret=LT), IMPLS["list"]["copy"]),
        "reversed": (sig(ret=LT), IMPLS["list"]["reversed"]),
        "sorted": (sig(ret=LT, after=_needs("Ints, Floats or Strs", is_comparable)), IMPLS["list"]["sorted"]),
        "sort": (sig(ret=NIL, after=_needs("Ints, Floats or Strs", is_comparable)), IMPLS["list"]["sort"]),
        "sum": (sig(ret=T, after=_needs("Ints or Floats", is_numeric)), IMPLS["list"]["sum"]),
        "min": (sig(ret=OptionT(T), after=_needs("Ints, Floats or Strs", is_comparable)), IMPLS["list"]["min"]),
        "max": (sig(ret=OptionT(T), after=_needs("Ints, Floats or Strs", is_comparable)), IMPLS["list"]["max"]),
        "join": (sig(STR, ret=STR, after=_needs("Strs (convert with .map(str) first)", lambda e: e == STR)),
                 IMPLS["list"]["join"]),
        "map": (generic(lambda U: MethodSig(FnT((FnT((T,), U),), ListT(U)))), IMPLS["list"]["map"]),
        "filter": (sig(FnT((T,), BOOL), ret=LT), IMPLS["list"]["filter"]),
        "find": (sig(FnT((T,), BOOL), ret=OptionT(T)), IMPLS["list"]["find"]),
        "any": (sig(FnT((T,), BOOL), ret=BOOL), IMPLS["list"]["any"]),
        "all": (sig(FnT((T,), BOOL), ret=BOOL), IMPLS["list"]["all"]),
        "count": (sig(FnT((T,), BOOL), ret=INT), IMPLS["list"]["count"]),
        "each": (sig(FnT((T,), NIL), ret=NIL), IMPLS["list"]["each"]),
        "reduce": (generic(lambda U: MethodSig(FnT((U, FnT((U, T), U)), U))), IMPLS["list"]["reduce"]),
        "sort_by": (generic(lambda U: MethodSig(FnT((FnT((T,), U),), LT),
                                                lambda _r, U=U: None if isinstance(prune(U), TypeVar) or is_comparable(U)
                                                else f"sort_by's key must be an Int, Float or Str, not {resolve(U)}")),
                    IMPLS["list"]["sort_by"]),
    }


def _str_methods() -> dict[str, tuple[Callable[[], MethodSig], Impl]]:
    def sig(*params: Type, ret: Type) -> Callable[[], MethodSig]:
        return lambda: MethodSig(FnT(tuple(params), ret))
    m = IMPLS["str"]
    return {
        "len": (sig(ret=INT), m["len"]), "is_empty": (sig(ret=BOOL), m["is_empty"]),
        "upper": (sig(ret=STR), m["upper"]), "lower": (sig(ret=STR), m["lower"]), "trim": (sig(ret=STR), m["trim"]),
        "split": (sig(STR, ret=ListT(STR)), m["split"]), "lines": (sig(ret=ListT(STR)), m["lines"]),
        "chars": (sig(ret=ListT(STR)), m["chars"]),
        "contains": (sig(STR, ret=BOOL), m["contains"]), "starts_with": (sig(STR, ret=BOOL), m["starts_with"]),
        "ends_with": (sig(STR, ret=BOOL), m["ends_with"]), "index_of": (sig(STR, ret=OptionT(INT)), m["index_of"]),
        "replace": (sig(STR, STR, ret=STR), m["replace"]), "repeat": (sig(INT, ret=STR), m["repeat"]),
        "slice": (sig(INT, INT, ret=STR), m["slice"]),
    }


def _map_methods(K: Type, V: Type) -> dict[str, tuple[Callable[[], MethodSig], Impl]]:
    def sig(*params: Type, ret: Type) -> Callable[[], MethodSig]:
        return lambda: MethodSig(FnT(tuple(params), ret))
    m = IMPLS["map"]
    return {
        "len": (sig(ret=INT), m["len"]), "is_empty": (sig(ret=BOOL), m["is_empty"]),
        "get": (sig(K, ret=OptionT(V)), m["get"]), "get_or": (sig(K, V, ret=V), m["get_or"]),
        "set": (sig(K, V, ret=NIL), m["set"]), "has": (sig(K, ret=BOOL), m["has"]),
        "remove": (sig(K, ret=OptionT(V)), m["remove"]), "keys": (sig(ret=ListT(K)), m["keys"]),
        "values": (sig(ret=ListT(V)), m["values"]), "copy": (sig(ret=MapT(K, V)), m["copy"]),
        "clear": (sig(ret=NIL), m["clear"]),
    }


def _int_methods() -> dict[str, tuple[Callable[[], MethodSig], Impl]]:
    m = IMPLS["int"]
    table: dict[str, tuple[Callable[[], MethodSig], Impl]] = {
        "abs": (lambda: MethodSig(FnT((), INT)), m["abs"]),
        "pow": (lambda: MethodSig(FnT((INT,), INT)), m["pow"]),
        "to_float": (lambda: MethodSig(FnT((), FLOAT)), m["to_float"]),
    }
    for op in ("add", "sub", "mul"):
        table[f"wrapping_{op}"] = (lambda: MethodSig(FnT((INT,), INT)), m[f"wrapping_{op}"])
        table[f"saturating_{op}"] = (lambda: MethodSig(FnT((INT,), INT)), m[f"saturating_{op}"])
    for op in ("add", "sub", "mul", "div"):
        table[f"checked_{op}"] = (lambda: MethodSig(FnT((INT,), OptionT(INT))), m[f"checked_{op}"])
    return table


def _float_methods() -> dict[str, tuple[Callable[[], MethodSig], Impl]]:
    m = IMPLS["float"]
    return {"abs": (lambda: MethodSig(FnT((), FLOAT)), m["abs"]),
            "floor": (lambda: MethodSig(FnT((), INT)), m["floor"]),
            "ceil": (lambda: MethodSig(FnT((), INT)), m["ceil"]),
            "round": (lambda: MethodSig(FnT((), INT)), m["round"]),
            "is_nan": (lambda: MethodSig(FnT((), BOOL)), m["is_nan"])}


IMPLS: dict[str, dict[str, Impl]] = {
    "list": {
        "len": lambda vm, xs: len(xs),
        "is_empty": lambda vm, xs: not xs,
        "push": lambda vm, xs, x: xs.append(x),
        "pop": lambda vm, xs: xs.pop() if xs else None,
        "insert": _list_insert,
        "remove": _list_remove,
        "extend": lambda vm, xs, ys: xs.extend(list(ys)),
        "clear": lambda vm, xs: xs.clear(),
        "contains": lambda vm, xs, x: x in xs,
        "index_of": lambda vm, xs, x: xs.index(x) if x in xs else None,
        "first": lambda vm, xs: xs[0] if xs else None,
        "last": lambda vm, xs: xs[-1] if xs else None,
        "slice": lambda vm, xs, a, b: (lambda r: xs[r[0]:r[1]])(_slice(len(xs), a, b)),
        "copy": lambda vm, xs: list(xs),
        "reversed": lambda vm, xs: xs[::-1],
        "sorted": lambda vm, xs: sorted(xs),
        "sort": lambda vm, xs: xs.sort(),
        "sum": _sum,
        "fsum": lambda vm, xs: float(sum(xs, 0.0)),
        "min": _min_max_list("min", min),
        "max": _min_max_list("max", max),
        "join": lambda vm, xs, sep: sep.join(xs),
        "map": lambda vm, xs, f: [vm.call_value(f, [x]) for x in list(xs)],
        "filter": lambda vm, xs, f: [x for x in list(xs) if vm.call_value(f, [x])],
        "find": lambda vm, xs, f: _find(vm, xs, f),
        "any": lambda vm, xs, f: _any(vm, xs, f),
        "all": lambda vm, xs, f: _all(vm, xs, f),
        "count": lambda vm, xs, f: len([x for x in list(xs) if vm.call_value(f, [x])]),
        "each": lambda vm, xs, f: _each(vm, xs, f),
        "reduce": lambda vm, xs, init, f: _reduce(vm, xs, init, f),
        "sort_by": lambda vm, xs, f: _sorted_key(vm, xs, f),
    },
    "str": {
        "len": lambda vm, s: len(s), "is_empty": lambda vm, s: not s,
        "upper": lambda vm, s: s.upper(), "lower": lambda vm, s: s.lower(), "trim": lambda vm, s: s.strip(),
        "split": _split, "lines": lambda vm, s: s.splitlines(), "chars": lambda vm, s: list(s),
        "contains": lambda vm, s, x: x in s, "starts_with": lambda vm, s, x: s.startswith(x),
        "ends_with": lambda vm, s, x: s.endswith(x), "index_of": _str_index_of,
        "replace": lambda vm, s, a, b: s.replace(a, b) if a else s, "repeat": _repeat,
        "slice": lambda vm, s, a, b: (lambda r: s[r[0]:r[1]])(_slice(len(s), a, b)),
    },
    "map": {
        "len": lambda vm, m: len(m), "is_empty": lambda vm, m: not m,
        "get": lambda vm, m, k: m.get(k), "get_or": _map_get_or,
        "set": lambda vm, m, k, v: m.__setitem__(k, v), "has": lambda vm, m, k: k in m,
        "remove": lambda vm, m, k: m.pop(k, None), "keys": lambda vm, m: list(m.keys()),
        "values": lambda vm, m: list(m.values()), "copy": lambda vm, m: dict(m), "clear": lambda vm, m: m.clear(),
    },
    "int": {"abs": lambda vm, n: check_int(abs(n)), "pow": _int_pow, "to_float": lambda vm, n: float(n),
            **{f"wrapping_{op}": _wrapping(op) for op in ("add", "sub", "mul")},
            **{f"saturating_{op}": _saturating(op) for op in ("add", "sub", "mul")},
            **{f"checked_{op}": _checked(op) for op in ("add", "sub", "mul", "div")}},
    "float": {"abs": lambda vm, x: abs(x), "floor": lambda vm, x: _int(vm, math.floor(x)) if math.isfinite(x) else _int(vm, x),
              "ceil": lambda vm, x: _int(vm, math.ceil(x)) if math.isfinite(x) else _int(vm, x),
              "round": _round, "is_nan": lambda vm, x: math.isnan(x)},
}


# Callbacks into ember are kept to plain Python loops and calls (no any()/next() over a
# generator, no *args): those go through C, and deep recursion through C runs into
# Python's C recursion limit, which is low on Windows.

def _find(vm: Any, xs: list, f: Any) -> Any:
    for x in list(xs):
        if vm.call_value(f, [x]):
            return x
    return None


def _any(vm: Any, xs: list, f: Any) -> bool:
    for x in list(xs):
        if vm.call_value(f, [x]):
            return True
    return False


def _all(vm: Any, xs: list, f: Any) -> bool:
    for x in list(xs):
        if not vm.call_value(f, [x]):
            return False
    return True


def _each(vm: Any, xs: list, f: Any) -> None:
    for x in list(xs):
        vm.call_value(f, [x])


def _reduce(vm: Any, xs: list, init: Any, f: Any) -> Any:
    acc = init
    for x in list(xs):
        acc = vm.call_value(f, [acc, x])
    return acc


def describe_builtins() -> str:
    """A text listing for `ember doc`-style help."""
    lines = [spec.doc for spec in BUILTIN_FUNCTIONS.values()]
    return "\n".join(lines)


_ = sys  # stdin/stdout come from the VM
