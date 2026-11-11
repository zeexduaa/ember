import dataclasses

import pytest

from ember import ast as A
from ember.source import EmberError


def sexpr(node) -> str:
    """A compact form of an expression for tests: (+ 1 (* 2 3))."""
    if isinstance(node, (A.IntLit, A.FloatLit)):
        return repr(node.value)
    if isinstance(node, A.StrLit):
        return repr(node.value)
    if isinstance(node, A.BoolLit):
        return "true" if node.value else "false"
    if isinstance(node, A.NilLit):
        return "nil"
    if isinstance(node, A.Name):
        return node.name
    if isinstance(node, A.Binary):
        return f"({node.op} {sexpr(node.left)} {sexpr(node.right)})"
    if isinstance(node, A.Unary):
        return f"({node.op} {sexpr(node.operand)})"
    if isinstance(node, A.RangeExpr):
        return f"({'..=' if node.inclusive else '..'} {sexpr(node.start)} {sexpr(node.end)})"
    if isinstance(node, A.Call):
        return f"(call {' '.join(sexpr(x) for x in [node.callee, *node.args])})"
    if isinstance(node, A.Index):
        return f"(index {sexpr(node.target)} {sexpr(node.index)})"
    if isinstance(node, A.Field):
        return f"(. {sexpr(node.target)} {node.name})"
    if isinstance(node, A.ListLit):
        return f"[{' '.join(sexpr(x) for x in node.items)}]"
    if isinstance(node, A.MapLit):
        return "{" + " ".join(f"{sexpr(k)}:{sexpr(v)}" for k, v in node.entries) + "}"
    if isinstance(node, A.Template):
        return "(template " + " ".join(repr(p) if isinstance(p, str) else sexpr(p) for p in node.parts) + ")"
    if isinstance(node, A.StructLit):
        return f"(struct {sexpr(node.path)} " + " ".join(f"{n}:{sexpr(v)}" for n, v, _ in node.fields) + ")"
    if dataclasses.is_dataclass(node):
        return type(node).__name__
    return repr(node)


def errors_of(fn, *args, **kwargs) -> list[str]:
    """Runs fn and returns the messages of the EmberError it raises."""
    with pytest.raises(EmberError) as exc:
        fn(*args, **kwargs)
    return [d.message for d in exc.value.diagnostics]
