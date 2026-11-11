"""Syntax tree. The parser builds it; the checker fills in ``type`` on expressions
and a few resolution details (``Field.kind`` etc.) that the compiler uses."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .source import Span

# -- type expressions (as written in the source) ---------------------------------


@dataclass
class TypeExpr:
    span: Span


@dataclass
class NamedType(TypeExpr):
    name: str                                   # "Int", "List", "Point", "geo.Point"
    args: list[TypeExpr] = field(default_factory=list)


@dataclass
class FnType(TypeExpr):
    params: list[TypeExpr]
    ret: TypeExpr | None


@dataclass
class OptionalType(TypeExpr):
    inner: TypeExpr


# -- expressions -----------------------------------------------------------------


@dataclass
class Expr:
    span: Span

    def __post_init__(self) -> None:
        self.type: Any = None     # set by the checker


@dataclass
class IntLit(Expr):
    value: int


@dataclass
class FloatLit(Expr):
    value: float


@dataclass
class StrLit(Expr):
    value: str


@dataclass
class BoolLit(Expr):
    value: bool


@dataclass
class NilLit(Expr):
    pass


@dataclass
class Template(Expr):
    parts: list[str | Expr]


@dataclass
class Name(Expr):
    name: str

    def __post_init__(self) -> None:
        super().__post_init__()
        self.binding: Any = None  # set by the checker: what the name refers to


@dataclass
class ListLit(Expr):
    items: list[Expr]


@dataclass
class MapLit(Expr):
    entries: list[tuple[Expr, Expr]]


@dataclass
class StructLit(Expr):
    path: Expr                                  # Name("Point") or Field(Name("geo"), "Point")
    fields: list[tuple[str, Expr, Span]]


@dataclass
class Unary(Expr):
    op: str                                     # "-" or "not"
    operand: Expr


@dataclass
class Binary(Expr):
    op: str
    left: Expr
    right: Expr


@dataclass
class RangeExpr(Expr):
    start: Expr
    end: Expr
    inclusive: bool


@dataclass
class Call(Expr):
    callee: Expr
    args: list[Expr]

    def __post_init__(self) -> None:
        super().__post_init__()
        self.kind: str = ""       # "function", "method", "builtin_method", "variant", "static"
        self.extra: Any = None


@dataclass
class Index(Expr):
    target: Expr
    index: Expr


@dataclass
class Field(Expr):
    target: Expr
    name: str
    name_span: Span

    def __post_init__(self) -> None:
        super().__post_init__()
        self.kind: str = ""       # "field", "variant", "module", "static", "method"
        self.extra: Any = None


@dataclass
class Param:
    name: str
    type: TypeExpr | None
    span: Span


@dataclass
class Lambda(Expr):
    params: list[Param]
    ret: TypeExpr | None
    body: Block

    def __post_init__(self) -> None:
        super().__post_init__()
        self.info: Any = None     # function info from the checker (captures, locals)


@dataclass
class IfExpr(Expr):
    cond: Expr
    then: Block
    else_: Block | IfExpr | None
    let_name: str | None = None                 # if let name = cond { ... }
    let_span: Span | None = None


@dataclass
class Pattern:
    span: Span


@dataclass
class WildcardPat(Pattern):
    pass


@dataclass
class BindPat(Pattern):
    name: str


@dataclass
class LitPat(Pattern):
    value: Expr                                 # IntLit, FloatLit, StrLit, BoolLit or NilLit


@dataclass
class VariantPat(Pattern):
    enum: str | None                            # "Shape" in Shape.Circle(r), else None
    variant: str
    args: list[Pattern] | None                  # None for a unit variant without parentheses

    def __post_init__(self) -> None:
        self.resolved: Any = None   # (enum type, variant index) from the checker


@dataclass
class MatchArm:
    pattern: Pattern
    guard: Expr | None
    body: Expr
    span: Span


@dataclass
class MatchExpr(Expr):
    subject: Expr
    arms: list[MatchArm]


@dataclass
class BlockExpr(Expr):
    block: Block


# -- statements ------------------------------------------------------------------


@dataclass
class Stmt:
    span: Span


@dataclass
class Block:
    stmts: list[Stmt]
    span: Span


@dataclass
class Let(Stmt):
    name: str
    name_span: Span
    type: TypeExpr | None
    value: Expr
    mutable: bool
    public: bool = False


@dataclass
class Assign(Stmt):
    target: Expr                                # Name, Index or Field
    op: str                                     # "=", "+=", "-=", "*=", "/=", "%="
    value: Expr


@dataclass
class ExprStmt(Stmt):
    expr: Expr


@dataclass
class Return(Stmt):
    value: Expr | None


@dataclass
class Break(Stmt):
    pass


@dataclass
class Continue(Stmt):
    pass


@dataclass
class While(Stmt):
    cond: Expr
    body: Block


@dataclass
class For(Stmt):
    var: str
    var_span: Span
    iterable: Expr
    body: Block


@dataclass
class FnDecl(Stmt):
    name: str
    name_span: Span
    type_params: list[tuple[str, Span]]
    params: list[Param]
    ret: TypeExpr | None
    body: Block
    public: bool = False
    has_self: bool = False

    def __post_init__(self) -> None:
        self.info: Any = None


@dataclass
class StructDecl(Stmt):
    name: str
    name_span: Span
    fields: list[Param]
    public: bool = False


@dataclass
class Variant:
    name: str
    fields: list[TypeExpr]
    span: Span


@dataclass
class EnumDecl(Stmt):
    name: str
    name_span: Span
    variants: list[Variant]
    public: bool = False


@dataclass
class TypeAlias(Stmt):
    name: str
    name_span: Span
    target: TypeExpr
    public: bool = False


@dataclass
class ImplDecl(Stmt):
    type_name: str
    name_span: Span
    methods: list[FnDecl]


@dataclass
class Import(Stmt):
    path: str
    alias: str
    path_span: Span

    def __post_init__(self) -> None:
        self.module_id: str = ""     # set by the module loader: which file this resolved to


@dataclass
class Program:
    stmts: list[Stmt]
    span: Span
