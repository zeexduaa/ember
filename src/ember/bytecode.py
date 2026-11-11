"""Opcodes and the disassembler.

Code is a flat list of ints: an opcode followed by its operands. Each frame has
numbered local slots (separate from its operand stack), so locals are read and
written by slot number, and a failed match pattern can jump away without
leaving values behind on the stack.

Arithmetic opcodes are typed (IADD for Ints, FADD for Floats, SADD for strings,
LADD for lists): the checker already knows the types, so the VM doesn't have to
look.
"""

from __future__ import annotations

from enum import IntEnum
from typing import Any

from .values import Closure, EnumValue, FunctionProto, NativeFn, StructType, EnumType, format_value


class Op(IntEnum):
    CONST = 0           # k                push constants[k]
    NIL = 1
    TRUE = 2
    FALSE = 3
    POP = 4
    DUP = 5
    GET_LOCAL = 6       # slot
    SET_LOCAL = 7       # slot            pops
    GET_UPVALUE = 8     # index
    SET_UPVALUE = 9     # index           pops
    GET_GLOBAL = 10     # k (name)
    SET_GLOBAL = 11     # k (name)        pops
    DEFINE_GLOBAL = 12  # k (name)        pops
    CLOSE_UPVALUE = 13  # slot            closes a captured local when its scope ends
    IADD = 14
    ISUB = 15
    IMUL = 16
    IDIV = 17
    IMOD = 18
    INEG = 19
    FADD = 20
    FSUB = 21
    FMUL = 22
    FDIV = 23
    FMOD = 24
    FNEG = 25
    SADD = 26
    LADD = 27
    NOT = 28
    EQ = 29
    NE = 30
    LT = 31
    LE = 32
    GT = 33
    GE = 34
    JUMP = 35                  # target
    JUMP_IF_FALSE = 36         # target       pops
    JUMP_IF_FALSE_OR_POP = 37  # target       for `and`: keeps false, else pops
    JUMP_IF_TRUE_OR_POP = 38   # target       for `or`
    JUMP_IF_NOT_NIL = 39       # target       for `??`: keeps the value, else pops it
    JUMP_IF_NIL = 40           # target       pops
    CALL = 41                  # argc         callee below the arguments
    CALL_BUILTIN = 42          # k argc       a built-in function by name
    CALL_METHOD = 43           # k argc       a built-in method (list.push, str.split, ...)
    RETURN = 44
    CLOSURE = 45               # k (proto)
    BUILD_LIST = 46            # n
    BUILD_MAP = 47             # n (pairs)
    BUILD_STR = 48             # n            joins n strings
    TO_STR = 49
    RANGE = 50                 # inclusive (0/1)
    INDEX_GET = 51
    INDEX_SET = 52             # stack: container, index, value
    GET_FIELD = 53             # index
    SET_FIELD = 54             # index        stack: struct, value
    MAKE_STRUCT = 55           # k (type) n
    MAKE_VARIANT = 56          # k (type) index n
    TEST_VARIANT = 57          # index        pops the value, pushes a Bool
    GET_PAYLOAD = 58           # index        pops an enum value, pushes one of its fields
    GET_ITER = 59
    FOR_ITER = 60              # target       next item, or jump when done (the iterator stays)
    GET_METHOD = 61            # k ((module, "Type.name"))
    DEFINE_METHOD = 62         # k            pops a closure
    MODULE_GET = 63            # k (name)     pops a module, pushes one of its globals
    MATCH_FAIL = 64


OPERANDS = {
    Op.CONST: 1, Op.GET_LOCAL: 1, Op.SET_LOCAL: 1, Op.GET_UPVALUE: 1, Op.SET_UPVALUE: 1, Op.GET_GLOBAL: 1,
    Op.SET_GLOBAL: 1, Op.DEFINE_GLOBAL: 1, Op.CLOSE_UPVALUE: 1, Op.JUMP: 1, Op.JUMP_IF_FALSE: 1,
    Op.JUMP_IF_FALSE_OR_POP: 1, Op.JUMP_IF_TRUE_OR_POP: 1, Op.JUMP_IF_NOT_NIL: 1, Op.JUMP_IF_NIL: 1,
    Op.CALL: 1, Op.CALL_BUILTIN: 2, Op.CALL_METHOD: 2, Op.CLOSURE: 1, Op.BUILD_LIST: 1, Op.BUILD_MAP: 1,
    Op.BUILD_STR: 1, Op.RANGE: 1, Op.GET_FIELD: 1, Op.SET_FIELD: 1, Op.MAKE_STRUCT: 2, Op.MAKE_VARIANT: 3,
    Op.TEST_VARIANT: 1, Op.GET_PAYLOAD: 1, Op.FOR_ITER: 1, Op.GET_METHOD: 1, Op.DEFINE_METHOD: 1,
    Op.MODULE_GET: 1,
}
CONSTANT_OPERAND = {Op.CONST, Op.GET_GLOBAL, Op.SET_GLOBAL, Op.DEFINE_GLOBAL, Op.CALL_BUILTIN, Op.CALL_METHOD,
                    Op.CLOSURE, Op.MAKE_STRUCT, Op.MAKE_VARIANT, Op.GET_METHOD, Op.DEFINE_METHOD, Op.MODULE_GET}


def show_constant(value: Any) -> str:
    if isinstance(value, FunctionProto):
        return f"<fn {value.name}>"
    if isinstance(value, (StructType, EnumType)):
        return value.name
    if isinstance(value, tuple):
        return ".".join(str(v) for v in value if v)
    if isinstance(value, (EnumValue, Closure, NativeFn)):
        return format_value(value)
    return format_value(value, quoted=True)


def disassemble(proto: FunctionProto, nested: bool = True) -> str:
    """A readable listing of a function's bytecode (and the functions it contains)."""
    out = [f"== {proto.name} (arity {proto.arity}, {proto.max_locals} locals"
           + (f", {len(proto.upvalues)} upvalues" if proto.upvalues else "") + ") =="]
    code = proto.code
    i = 0
    last_line = -1
    children: list[FunctionProto] = []
    while i < len(code):
        op = Op(code[i])
        n = OPERANDS.get(op, 0)
        args = code[i + 1:i + 1 + n]
        line = proto.lines[i]
        line_text = f"{line:4d}" if line != last_line else "   |"
        last_line = line
        shown = []
        for j, arg in enumerate(args):
            if j == 0 and op in CONSTANT_OPERAND:
                constant = proto.constants[arg]
                shown.append(f"{arg} ({show_constant(constant)})")
                if isinstance(constant, FunctionProto):
                    children.append(constant)
            else:
                shown.append(str(arg))
        out.append(f"{i:5d} {line_text}  {op.name:<22}{' '.join(shown)}".rstrip())
        i += 1 + n
    if nested:
        for child in children:
            out.append("")
            out.append(disassemble(child))
    return "\n".join(out)
