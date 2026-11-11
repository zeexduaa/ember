"""Types and unification.

Most types are immutable values (``Int``, ``List[Int]``, ``fn(Int) -> Bool``).
Structs, enums and modules are compared by identity: two structs with the same
fields are still different types.

Inference uses type variables: ``[]`` is ``List[?1]`` until something decides
``?1``. :func:`unify` binds variables; :func:`prune` follows bindings.
Generic functions use :class:`TypeParam` (rigid inside the function body) and
are instantiated with fresh variables at each use.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any


class Type:
    pass


@dataclass(frozen=True)
class Prim(Type):
    name: str

    def __str__(self) -> str:
        return self.name


INT = Prim("Int")
FLOAT = Prim("Float")
BOOL = Prim("Bool")
STR = Prim("Str")
NIL = Prim("Nil")
RANGE = Prim("Range")
NEVER = Prim("Never")      # the type of panic(...): fits anywhere
PRIMS = {"Int": INT, "Float": FLOAT, "Bool": BOOL, "Str": STR, "Nil": NIL, "Range": RANGE}

_ids = itertools.count(1)


@dataclass(eq=False)
class TypeVar(Type):
    """An unknown type that unification can fill in."""
    id: int = field(default_factory=lambda: next(_ids))
    ref: Type | None = None

    def __str__(self) -> str:
        return str(prune(self)) if self.ref is not None else "_"

    def __hash__(self) -> int:
        return id(self)


@dataclass(frozen=True)
class TypeParam(Type):
    """A generic function's type parameter, like T in fn first[T](xs: List[T]) -> T?"""
    name: str
    owner: int                 # which declaration it belongs to

    def __str__(self) -> str:
        return self.name


@dataclass(frozen=True)
class ListT(Type):
    elem: Type

    def __str__(self) -> str:
        return f"List[{self.elem}]"


@dataclass(frozen=True)
class MapT(Type):
    key: Type
    value: Type

    def __str__(self) -> str:
        return f"Map[{self.key}, {self.value}]"


@dataclass(frozen=True)
class FnT(Type):
    params: tuple[Type, ...]
    ret: Type

    def __str__(self) -> str:
        return f"fn({', '.join(map(str, self.params))}) -> {self.ret}"


@dataclass(frozen=True)
class OptionT(Type):
    inner: Type

    def __str__(self) -> str:
        inner = str(self.inner)
        return f"({inner})?" if isinstance(prune(self.inner), FnT) else f"{inner}?"


@dataclass
class Method:
    name: str
    type: FnT                  # without self
    has_self: bool
    type_params: list[TypeParam]
    decl: Any = None           # the FnDecl


class StructT(Type):
    def __init__(self, name: str, module: str = ""):
        self.name = name
        self.module = module
        self.fields: dict[str, Type] = {}
        self.methods: dict[str, Method] = {}

    def field_index(self, name: str) -> int:
        return list(self.fields).index(name)

    def __str__(self) -> str:
        return self.name

    def __repr__(self) -> str:
        return f"StructT({self.name})"


class EnumT(Type):
    def __init__(self, name: str, module: str = ""):
        self.name = name
        self.module = module
        self.variants: list[tuple[str, tuple[Type, ...]]] = []
        self.methods: dict[str, Method] = {}

    def variant(self, name: str) -> tuple[int, tuple[Type, ...]] | None:
        for i, (vname, fields) in enumerate(self.variants):
            if vname == name:
                return i, fields
        return None

    def __str__(self) -> str:
        return self.name

    def __repr__(self) -> str:
        return f"EnumT({self.name})"


class ModuleT(Type):
    def __init__(self, name: str, path: str):
        self.name = name
        self.path = path
        self.members: dict[str, Any] = {}    # name -> Binding

    def __str__(self) -> str:
        return f"module {self.name}"


def prune(t: Type) -> Type:
    while isinstance(t, TypeVar) and t.ref is not None:
        t = t.ref
    return t


def resolve(t: Type) -> Type:
    """Replaces every bound variable inside t with what it's bound to."""
    t = prune(t)
    if isinstance(t, ListT):
        return ListT(resolve(t.elem))
    if isinstance(t, MapT):
        return MapT(resolve(t.key), resolve(t.value))
    if isinstance(t, FnT):
        return FnT(tuple(resolve(p) for p in t.params), resolve(t.ret))
    if isinstance(t, OptionT):
        return OptionT(resolve(t.inner))
    return t


def occurs(var: TypeVar, t: Type) -> bool:
    t = prune(t)
    if t is var:
        return True
    if isinstance(t, ListT):
        return occurs(var, t.elem)
    if isinstance(t, MapT):
        return occurs(var, t.key) or occurs(var, t.value)
    if isinstance(t, FnT):
        return any(occurs(var, p) for p in t.params) or occurs(var, t.ret)
    if isinstance(t, OptionT):
        return occurs(var, t.inner)
    return False


def unify(a: Type, b: Type) -> bool:
    """Makes a and b the same type if possible, binding variables. False if they can't be."""
    a, b = prune(a), prune(b)
    if a is b:
        return True
    if isinstance(a, TypeVar):
        if occurs(a, b):
            return False
        a.ref = b
        return True
    if isinstance(b, TypeVar):
        return unify(b, a)
    if a == NEVER or b == NEVER:
        return True
    if isinstance(a, ListT) and isinstance(b, ListT):
        return unify(a.elem, b.elem)
    if isinstance(a, MapT) and isinstance(b, MapT):
        return unify(a.key, b.key) and unify(a.value, b.value)
    if isinstance(a, OptionT) and isinstance(b, OptionT):
        return unify(a.inner, b.inner)
    if isinstance(a, FnT) and isinstance(b, FnT):
        return (len(a.params) == len(b.params) and all(unify(x, y) for x, y in zip(a.params, b.params))
                and unify(a.ret, b.ret))
    return a == b


def substitute(t: Type, mapping: dict[TypeParam, Type]) -> Type:
    t = prune(t)
    if isinstance(t, TypeParam):
        return mapping.get(t, t)
    if isinstance(t, ListT):
        return ListT(substitute(t.elem, mapping))
    if isinstance(t, MapT):
        return MapT(substitute(t.key, mapping), substitute(t.value, mapping))
    if isinstance(t, FnT):
        return FnT(tuple(substitute(p, mapping) for p in t.params), substitute(t.ret, mapping))
    if isinstance(t, OptionT):
        return OptionT(substitute(t.inner, mapping))
    return t


def has_unknowns(t: Type) -> bool:
    t = prune(t)
    if isinstance(t, TypeVar):
        return True
    if isinstance(t, ListT):
        return has_unknowns(t.elem)
    if isinstance(t, MapT):
        return has_unknowns(t.key) or has_unknowns(t.value)
    if isinstance(t, FnT):
        return any(has_unknowns(p) for p in t.params) or has_unknowns(t.ret)
    if isinstance(t, OptionT):
        return has_unknowns(t.inner)
    return False


def is_numeric(t: Type) -> bool:
    return prune(t) in (INT, FLOAT)


def is_comparable(t: Type) -> bool:
    """Types that < and sorting work on."""
    return prune(t) in (INT, FLOAT, STR)


def is_hashable(t: Type) -> bool:
    """Types that can be map keys."""
    return prune(t) in (INT, STR, BOOL)
