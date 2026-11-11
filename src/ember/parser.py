"""Builds the syntax tree from tokens.

Statements are parsed by recursive descent, expressions by precedence climbing
(a Pratt parser). From loosest to tightest:

    ??          a ?? b          (right-associative)
    or
    and
    not         not a           (prefix)
    == != < <= > >=             (no chaining: a < b < c is an error)
    .. ..=      0..10
    + -
    * / %
    -           -x              (prefix)
    calls, indexing, fields: f(x)  xs[0]  p.x

The parser recovers from errors at the next statement, so one run reports
several mistakes instead of stopping at the first.
"""

from __future__ import annotations

from . import ast as A
from .lexer import Lexer, Token
from .source import Diagnostic, EmberError, Source, Span

BINARY_PRECEDENCE = {
    "??": 1, "or": 2, "and": 3,
    "==": 5, "!=": 5, "<": 5, "<=": 5, ">": 5, ">=": 5,
    "..": 6, "..=": 6,
    "+": 7, "-": 7,
    "*": 8, "/": 8, "%": 8,
}
NOT_PRECEDENCE = 4
UNARY_PRECEDENCE = 9
NON_ASSOCIATIVE = {5, 6}
ASSIGN_OPS = {"=", "+=", "-=", "*=", "/=", "%="}
MAX_ERRORS = 25


class ParseError(Exception):
    def __init__(self, message: str, span: Span, notes: list[str] | None = None):
        super().__init__(message)
        self.diagnostic = Diagnostic(message, span, notes=notes or [])


class Parser:
    def __init__(self, tokens: list[Token], source: Source):
        self.tokens = tokens
        self.source = source
        self.pos = 0
        self.errors: list[Diagnostic] = []
        self.no_struct = False   # true while parsing `if`/`while`/`for`/`match` heads

    # -- token helpers -------------------------------------------------------------

    @property
    def tok(self) -> Token:
        return self.tokens[self.pos]

    def peek(self, offset: int = 1) -> Token:
        return self.tokens[min(self.pos + offset, len(self.tokens) - 1)]

    def at(self, *kinds: str) -> bool:
        return self.tok.kind in kinds

    def advance(self) -> Token:
        token = self.tok
        if token.kind != "eof":
            self.pos += 1
        return token

    def accept(self, kind: str) -> Token | None:
        return self.advance() if self.tok.kind == kind else None

    def expect(self, kind: str, what: str | None = None) -> Token:
        if self.tok.kind == kind:
            return self.advance()
        raise ParseError(f"expected {what or repr(kind)}, found {self.describe(self.tok)}", self.tok.span)

    def skip_newlines(self) -> None:
        while self.tok.kind in ("newline", ";"):
            self.pos += 1

    @staticmethod
    def describe(token: Token) -> str:
        if token.kind == "eof":
            return "end of file"
        if token.kind == "newline":
            return "end of line"
        if token.kind in ("ident", "int", "float"):
            return f"{token.kind if token.kind != 'ident' else 'name'} {token.text!r}"
        if token.kind in ("str", "template"):
            return "a string"
        return repr(token.text)

    # -- program and statements ------------------------------------------------------

    def parse_program(self) -> A.Program:
        stmts: list[A.Stmt] = []
        self.skip_newlines()
        while not self.at("eof"):
            stmt = self._recovering(self.statement, top_level=True)
            if stmt is not None:
                stmts.append(stmt)
            self.skip_newlines()
        if self.errors:
            raise EmberError(self.errors, self.source)
        return A.Program(stmts, Span(0, len(self.source.text)))

    def _recovering(self, parse, top_level: bool = False):
        start = self.pos
        try:
            stmt = parse(top_level=top_level)
            if not self.at("newline", ";", "}", "eof"):
                raise ParseError(f"expected end of line after the statement, found {self.describe(self.tok)}",
                                 self.tok.span)
            return stmt
        except ParseError as exc:
            self.errors.append(exc.diagnostic)
            if len(self.errors) >= MAX_ERRORS:
                raise EmberError(self.errors, self.source) from None
            self._synchronize(start)
            return None

    def _synchronize(self, start: int) -> None:
        """Skips to the end of the broken statement: the next newline or ';' outside brackets,
        or a '}' that closes an enclosing block."""
        if self.pos == start:
            self.advance()
        depth = 0
        while not self.at("eof"):
            kind = self.tok.kind
            if kind in ("(", "[", "{"):
                depth += 1
            elif kind in (")", "]"):
                depth = max(0, depth - 1)
            elif kind == "}":
                if depth == 0:
                    return
                depth -= 1
            elif kind in ("newline", ";") and depth == 0:
                return
            self.advance()

    def statement(self, top_level: bool = False) -> A.Stmt:
        tok = self.tok
        public = False
        if tok.kind == "pub":
            if not top_level:
                raise ParseError("'pub' only goes on top-level declarations", tok.span)
            self.advance()
            public = True
            if not self.at("fn", "struct", "enum", "let", "var") and not self._at_type_alias():
                raise ParseError(f"expected fn, struct, enum, type, let or var after 'pub', found {self.describe(self.tok)}",
                                 self.tok.span)
            tok = self.tok
        kind = tok.kind
        if kind in ("let", "var"):
            return self.let_stmt(public)
        if kind == "fn" and self.peek().kind == "ident":
            return self.fn_decl(public)
        if self._at_type_alias():
            return self.type_alias(public, top_level)
        if kind == "struct":
            return self.struct_decl(public, top_level)
        if kind == "enum":
            return self.enum_decl(public, top_level)
        if kind == "impl":
            if not top_level:
                raise ParseError("'impl' blocks go at the top level", tok.span)
            return self.impl_decl()
        if kind == "import":
            if not top_level:
                raise ParseError("imports go at the top level", tok.span)
            return self.import_stmt()
        if kind == "return":
            self.advance()
            value = None if self.at("newline", ";", "}", "eof") else self.expression()
            return A.Return(tok.span.to(value.span) if value else tok.span, value)
        if kind == "break":
            return A.Break(self.advance().span)
        if kind == "continue":
            return A.Continue(self.advance().span)
        if kind == "while":
            self.advance()
            cond = self.head_expression()
            body = self.block()
            return A.While(tok.span.to(body.span), cond, body)
        if kind == "for":
            self.advance()
            name = self.expect("ident", "a loop variable name")
            self.expect("in", "'in'")
            iterable = self.head_expression()
            body = self.block()
            return A.For(tok.span.to(body.span), name.text, name.span, iterable, body)
        expr = self.expression()
        if self.tok.kind in ASSIGN_OPS:
            op = self.advance()
            if not isinstance(expr, (A.Name, A.Index, A.Field)):
                raise ParseError("can only assign to a variable, an element (xs[i]) or a field (p.x)", expr.span)
            value = self.expression()
            return A.Assign(expr.span.to(value.span), expr, op.kind, value)
        return A.ExprStmt(expr.span, expr)

    def let_stmt(self, public: bool) -> A.Let:
        keyword = self.advance()
        name = self.expect("ident", "a variable name")
        type_expr = self.type_expr() if self.accept(":") else None
        if not self.at("="):
            raise ParseError(f"'{keyword.text} {name.text}' needs a value: {keyword.text} {name.text} = ...",
                             self.tok.span)
        self.advance()
        value = self.expression()
        return A.Let(keyword.span.to(value.span), name.text, name.span, type_expr, value,
                     keyword.kind == "var", public)

    def fn_decl(self, public: bool, in_impl: bool = False) -> A.FnDecl:
        keyword = self.expect("fn")
        name = self.expect("ident", "a function name")
        type_params = []
        if self.accept("["):
            while not self.at("]"):
                param = self.expect("ident", "a type parameter name")
                type_params.append((param.text, param.span))
                if not self.accept(","):
                    break
            self.expect("]", "']'")
        self.expect("(", "'('")
        params, has_self = self.params(allow_self=in_impl)
        ret = self.type_expr() if self.accept("->") else None
        body = self.block()
        return A.FnDecl(keyword.span.to(body.span), name.text, name.span, type_params, params, ret, body,
                        public, has_self)

    def params(self, allow_self: bool = False, allow_untyped: bool = False) -> tuple[list[A.Param], bool]:
        params: list[A.Param] = []
        has_self = False
        while not self.at(")"):
            if self.at("self"):
                tok = self.advance()
                if not allow_self:
                    raise ParseError("'self' is only a parameter of methods in an impl block", tok.span)
                if params or has_self:
                    raise ParseError("'self' must be the first parameter", tok.span)
                has_self = True
            else:
                name = self.expect("ident", "a parameter name")
                type_expr = None
                if self.accept(":"):
                    type_expr = self.type_expr()
                elif not allow_untyped:
                    raise ParseError(f"parameter '{name.text}' needs a type, e.g. {name.text}: Int", self.tok.span)
                if any(p.name == name.text for p in params):
                    raise ParseError(f"two parameters are named '{name.text}'", name.span)
                params.append(A.Param(name.text, type_expr, name.span.to(type_expr.span) if type_expr else name.span))
            if not self.accept(","):
                break
        self.expect(")", "')' after the parameters")
        return params, has_self

    def _at_type_alias(self) -> bool:
        """`type Name = ...`. 'type' isn't a keyword, so it stays usable as an ordinary name."""
        return (self.tok.kind == "ident" and self.tok.text == "type" and self.peek().kind == "ident"
                and self.peek(2).kind == "=")

    def type_alias(self, public: bool, top_level: bool) -> A.TypeAlias:
        keyword = self.advance()
        if not top_level:
            raise ParseError("type aliases are declared at the top level", keyword.span)
        name = self.advance()
        self.expect("=")
        target = self.type_expr()
        return A.TypeAlias(keyword.span.to(target.span), name.text, name.span, target, public)

    def struct_decl(self, public: bool, top_level: bool) -> A.StructDecl:
        keyword = self.advance()
        if not top_level:
            raise ParseError("structs are declared at the top level", keyword.span)
        name = self.expect("ident", "a struct name")
        self.expect("{", "'{'")
        fields: list[A.Param] = []
        self.skip_newlines()
        while not self.at("}"):
            field_name = self.expect("ident", "a field name")
            self.expect(":", "':' and the field's type")
            type_expr = self.type_expr()
            if any(f.name == field_name.text for f in fields):
                raise ParseError(f"two fields are named '{field_name.text}'", field_name.span)
            fields.append(A.Param(field_name.text, type_expr, field_name.span.to(type_expr.span)))
            self.separator("after a field")
        end = self.expect("}")
        return A.StructDecl(keyword.span.to(end.span), name.text, name.span, fields, public)

    def enum_decl(self, public: bool, top_level: bool) -> A.EnumDecl:
        keyword = self.advance()
        if not top_level:
            raise ParseError("enums are declared at the top level", keyword.span)
        name = self.expect("ident", "an enum name")
        self.expect("{", "'{'")
        variants: list[A.Variant] = []
        self.skip_newlines()
        while not self.at("}"):
            variant = self.expect("ident", "a variant name")
            if not variant.text[0].isupper():
                raise ParseError(f"variant names start with a capital letter: {variant.text.capitalize()}",
                                 variant.span)
            types: list[A.TypeExpr] = []
            end_span = variant.span
            if self.accept("("):
                while not self.at(")"):
                    types.append(self.type_expr())
                    if not self.accept(","):
                        break
                end_span = self.expect(")", "')'").span
            if any(v.name == variant.text for v in variants):
                raise ParseError(f"two variants are named '{variant.text}'", variant.span)
            variants.append(A.Variant(variant.text, types, variant.span.to(end_span)))
            self.separator("after a variant")
        end = self.expect("}")
        if not variants:
            raise ParseError(f"enum {name.text} needs at least one variant", name.span)
        return A.EnumDecl(keyword.span.to(end.span), name.text, name.span, variants, public)

    def impl_decl(self) -> A.ImplDecl:
        keyword = self.advance()
        name = self.expect("ident", "the name of a struct or enum")
        self.expect("{", "'{'")
        methods: list[A.FnDecl] = []
        self.skip_newlines()
        while not self.at("}", "eof"):
            if not self.at("fn"):
                raise ParseError(f"impl blocks contain methods (fn ...), found {self.describe(self.tok)}",
                                 self.tok.span)
            methods.append(self.fn_decl(public=True, in_impl=True))
            self.skip_newlines()
        end = self.expect("}")
        return A.ImplDecl(keyword.span.to(end.span), name.text, name.span, methods)

    def import_stmt(self) -> A.Import:
        keyword = self.advance()
        path = self.expect("str", 'a module path in quotes, e.g. import "geometry"')
        if self.accept("as"):
            alias = self.expect("ident", "a name for the module").text
        else:
            alias = path.value.replace("\\", "/").rstrip("/").split("/")[-1].removesuffix(".em")
            if not alias.isidentifier():
                raise ParseError(f"can't make a name from {path.value!r}; add 'as name'", path.span)
        return A.Import(keyword.span.to(path.span), path.value, alias, path.span)

    def separator(self, where: str) -> None:
        """Between items in { }: a comma, a line break, or the closing brace."""
        if self.accept(",") or self.at("newline", ";"):
            self.skip_newlines()
        elif not self.at("}"):
            raise ParseError(f"expected ',', a new line or '}}' {where}, found {self.describe(self.tok)}",
                             self.tok.span)

    def block(self) -> A.Block:
        start = self.expect("{", "'{'")
        stmts: list[A.Stmt] = []
        self.skip_newlines()
        while not self.at("}", "eof"):
            stmt = self._recovering(self.statement)
            if stmt is not None:
                stmts.append(stmt)
            self.skip_newlines()
        end = self.expect("}", "'}'")
        return A.Block(stmts, start.span.to(end.span))

    # -- types -----------------------------------------------------------------------

    def type_expr(self) -> A.TypeExpr:
        tok = self.tok
        if tok.kind == "fn":
            self.advance()
            self.expect("(", "'(' for a function type")
            params = []
            while not self.at(")"):
                params.append(self.type_expr())
                if not self.accept(","):
                    break
            end = self.expect(")", "')'")
            ret = self.type_expr() if self.accept("->") else None
            result: A.TypeExpr = A.FnType(tok.span.to(ret.span if ret else end.span), params, ret)
        elif tok.kind in ("ident", "nil"):
            self.advance()
            name = "Nil" if tok.kind == "nil" else tok.text
            span = tok.span
            while self.at(".") and self.peek().kind == "ident":   # module.Type
                self.advance()
                part = self.advance()
                name += "." + part.text
                span = span.to(part.span)
            args = []
            if self.accept("["):
                while not self.at("]"):
                    args.append(self.type_expr())
                    if not self.accept(","):
                        break
                span = span.to(self.expect("]", "']'").span)
            result = A.NamedType(span, name, args)
        elif tok.kind == "(":
            self.advance()
            inner = self.type_expr()
            self.expect(")", "')'")
            result = inner
        else:
            raise ParseError(f"expected a type, found {self.describe(tok)}", tok.span,
                             notes=["types look like Int, Str, List[Int], Map[Str, Int], fn(Int) -> Bool, Int?"])
        while self.at("?"):
            q = self.advance()
            result = A.OptionalType(result.span.to(q.span), result)
        return result

    # -- expressions -------------------------------------------------------------------

    def head_expression(self) -> A.Expr:
        """The condition of if/while, a for loop's iterable or a match subject: `Name {` is a block, not a struct."""
        saved = self.no_struct
        self.no_struct = True
        try:
            return self.expression()
        finally:
            self.no_struct = saved

    def nested_expression(self) -> A.Expr:
        """An expression inside ( ), [ ] or a map literal: a `{` there can't start the if/for block,
        so struct literals are allowed again (for p in [Point { x: 0 }] { ... })."""
        saved = self.no_struct
        self.no_struct = False
        try:
            return self.expression()
        finally:
            self.no_struct = saved

    def expression(self, min_prec: int = 0) -> A.Expr:
        left = self.prefix()
        while True:
            op = self.tok.kind
            if op in ("|", "||"):
                raise ParseError(f"'{op}' isn't an operator in ember; use 'or'", self.tok.span,
                                 notes=["| only separates alternatives in a match pattern: 1 | 2 => ..."])
            prec = BINARY_PRECEDENCE.get(op)
            if prec is None or prec < min_prec:
                return left
            self.advance()
            # ?? is right-associative; everything else is left-associative.
            right = self.expression(prec if op == "??" else prec + 1)
            if prec in NON_ASSOCIATIVE and BINARY_PRECEDENCE.get(self.tok.kind) == prec:
                raise ParseError(f"'{self.tok.kind}' can't be chained after '{op}'; add parentheses", self.tok.span,
                                 notes=["write (a < b) and (b < c) instead of a < b < c"] if prec == 5 else [])
            span = left.span.to(right.span)
            if op in ("..", "..="):
                left = A.RangeExpr(span, left, right, op == "..=")
            else:
                left = A.Binary(span, op, left, right)

    def prefix(self) -> A.Expr:
        tok = self.tok
        if tok.kind == "-":
            self.advance()
            operand = self.expression(UNARY_PRECEDENCE)
            return A.Unary(tok.span.to(operand.span), "-", operand)
        if tok.kind == "not":
            self.advance()
            operand = self.expression(NOT_PRECEDENCE)
            return A.Unary(tok.span.to(operand.span), "not", operand)
        return self.postfix(self.primary())

    def postfix(self, expr: A.Expr) -> A.Expr:
        while True:
            tok = self.tok
            if tok.kind == "(":
                self.advance()
                args = []
                while not self.at(")"):
                    args.append(self.nested_expression())
                    if not self.accept(","):
                        break
                end = self.expect(")", "')' or ',' in the call")
                expr = A.Call(expr.span.to(end.span), expr, args)
            elif tok.kind == "[":
                self.advance()
                index = self.nested_expression()
                end = self.expect("]", "']'")
                expr = A.Index(expr.span.to(end.span), expr, index)
            elif tok.kind == ".":
                self.advance()
                name = self.tok
                if name.kind not in ("ident",):
                    raise ParseError(f"expected a field or method name after '.', found {self.describe(name)}",
                                     name.span)
                self.advance()
                expr = A.Field(expr.span.to(name.span), expr, name.text, name.span)
            elif tok.kind == "{" and not self.no_struct and self._is_type_path(expr):
                expr = self.struct_literal(expr)
            else:
                return expr

    @staticmethod
    def _is_type_path(expr: A.Expr) -> bool:
        if isinstance(expr, A.Name):
            return expr.name[:1].isupper()
        if isinstance(expr, A.Field):
            return expr.name[:1].isupper() and isinstance(expr.target, A.Name)
        return False

    def struct_literal(self, path: A.Expr) -> A.StructLit:
        self.expect("{")
        fields: list[tuple[str, A.Expr, Span]] = []
        self.skip_newlines()
        while not self.at("}"):
            name = self.expect("ident", "a field name")
            if self.accept(":"):
                value = self.expression()
            else:   # Point { x, y } is short for Point { x: x, y: y }
                value = A.Name(name.span, name.text)
            fields.append((name.text, value, name.span))
            self.skip_newlines()
            if not self.accept(","):
                self.skip_newlines()
                if not self.at("}"):
                    raise ParseError(f"expected ',' or '}}' in the struct literal, found {self.describe(self.tok)}",
                                     self.tok.span)
            self.skip_newlines()
        end = self.expect("}")
        return A.StructLit(path.span.to(end.span), path, fields)

    def primary(self) -> A.Expr:
        tok = self.tok
        kind = tok.kind
        if kind == "int":
            self.advance()
            return A.IntLit(tok.span, tok.value)
        if kind == "float":
            self.advance()
            return A.FloatLit(tok.span, tok.value)
        if kind == "str":
            self.advance()
            return A.StrLit(tok.span, tok.value)
        if kind == "template":
            self.advance()
            return self.template(tok)
        if kind in ("true", "false"):
            self.advance()
            return A.BoolLit(tok.span, kind == "true")
        if kind == "nil":
            self.advance()
            return A.NilLit(tok.span)
        if kind in ("ident", "self"):
            self.advance()
            return A.Name(tok.span, tok.text)
        if kind == "(":
            self.advance()
            saved = self.no_struct
            self.no_struct = False
            try:
                inner = self.expression()
            finally:
                self.no_struct = saved
            self.expect(")", "')'")
            return inner
        if kind == "[":
            self.advance()
            items = []
            while not self.at("]"):
                items.append(self.nested_expression())
                if not self.accept(","):
                    break
            end = self.expect("]", "']' or ',' in the list")
            return A.ListLit(tok.span.to(end.span), items)
        if kind == "{":
            return self.map_literal()
        if kind == "fn":
            return self.lambda_expr()
        if kind == "if":
            return self.if_expr()
        if kind == "match":
            return self.match_expr()
        notes = []
        if kind == "newline":
            notes = ["the expression continues on the next line; end the line with an operator or wrap it in ( )"]
        raise ParseError(f"expected an expression, found {self.describe(tok)}", tok.span, notes)

    def template(self, tok: Token) -> A.Template:
        parts: list[str | A.Expr] = []
        for part in tok.value:
            if isinstance(part, str):
                parts.append(part)
                continue
            sub = Parser(part, self.source)
            try:
                expr = sub.expression()
                if not sub.at("eof"):
                    raise ParseError(f"unexpected {sub.describe(sub.tok)} inside ${{...}}", sub.tok.span)
            except ParseError as exc:
                raise exc
            parts.append(expr)
        return A.Template(tok.span, parts)

    def map_literal(self) -> A.MapLit:
        start = self.expect("{")
        entries = []
        self.skip_newlines()
        while not self.at("}"):
            key = self.nested_expression()
            self.expect(":", "':' between a key and its value")
            value = self.nested_expression()
            entries.append((key, value))
            self.skip_newlines()
            if not self.accept(","):
                self.skip_newlines()
                if not self.at("}"):
                    raise ParseError(f"expected ',' or '}}' in the map, found {self.describe(self.tok)}",
                                     self.tok.span)
            self.skip_newlines()
        end = self.expect("}")
        return A.MapLit(start.span.to(end.span), entries)

    def struct_pattern(self, name: Token) -> A.StructPat:
        """Point { x: 0.0, y } or Point { x, .. }"""
        self.expect("{")
        fields: list[tuple[str, A.Pattern, Span]] = []
        rest = False
        self.skip_newlines()
        while not self.at("}"):
            if self.accept(".."):
                rest = True
                self.skip_newlines()
                break
            field = self.expect("ident", "a field name or ..")
            pat = self.pattern() if self.accept(":") else A.BindPat(field.span, field.text)
            fields.append((field.text, pat, field.span))
            self.skip_newlines()
            if not self.accept(","):
                break
            self.skip_newlines()
        end = self.expect("}", "'}' to end the struct pattern")
        return A.StructPat(name.span.to(end.span), name.text, fields, rest)

    def literal_pattern_value(self) -> A.Expr:
        start = self.tok
        negative = bool(self.accept("-"))
        lit = self.primary()
        if negative:
            lit = (A.IntLit(start.span.to(lit.span), -lit.value) if isinstance(lit, A.IntLit)
                   else A.FloatLit(start.span.to(lit.span), -lit.value))
        return lit

    def lambda_expr(self) -> A.Lambda:
        keyword = self.expect("fn")
        self.expect("(", "'(' (a function value looks like fn(x: Int) -> Int { x + 1 })")
        params, _ = self.params(allow_untyped=True)   # types can come from where the function is used
        ret = self.type_expr() if self.accept("->") else None
        saved = self.no_struct
        self.no_struct = False
        try:
            body = self.block()
        finally:
            self.no_struct = saved
        return A.Lambda(keyword.span.to(body.span), params, ret, body)

    def if_expr(self) -> A.IfExpr:
        keyword = self.expect("if")
        let_name = let_span = None
        if self.accept("let"):
            name = self.expect("ident", "a name for the value")
            let_name, let_span = name.text, name.span
            self.expect("=", "'='")
        cond = self.head_expression()
        then = self.block()
        else_: A.Block | A.IfExpr | None = None
        # `else` may follow on the next line: } \n else { ... }
        save = self.pos
        self.skip_newlines()
        if self.at("else"):
            self.advance()
            else_ = self.if_expr() if self.at("if") else self.block()
        else:
            self.pos = save
        end = else_.span if else_ is not None else then.span
        return A.IfExpr(keyword.span.to(end), cond, then, else_, let_name, let_span)

    def match_expr(self) -> A.MatchExpr:
        keyword = self.expect("match")
        subject = self.head_expression()
        self.expect("{", "'{' to start the match arms")
        arms: list[A.MatchArm] = []
        self.skip_newlines()
        while not self.at("}", "eof"):
            pattern = self.pattern()
            guard = self.expression() if self.accept("if") else None
            self.expect("=>", "'=>' after the pattern")
            self.skip_newlines()
            if self.at("{"):
                block = self.block()
                body: A.Expr = A.BlockExpr(block.span, block)
            else:
                body = self.expression()
            arms.append(A.MatchArm(pattern, guard, body, pattern.span.to(body.span)))
            self.separator("between match arms")
        end = self.expect("}", "'}' to end the match")
        if not arms:
            raise ParseError("a match needs at least one arm", keyword.span)
        return A.MatchExpr(keyword.span.to(end.span), subject, arms)

    def pattern(self) -> A.Pattern:
        """A pattern, or several separated by | (any of them may match)."""
        first = self.single_pattern()
        if not self.at("|"):
            return first
        options = [first]
        while self.accept("|"):
            options.append(self.single_pattern())
        return A.OrPat(first.span.to(options[-1].span), options)

    def single_pattern(self) -> A.Pattern:
        tok = self.tok
        if tok.kind == "ident" and tok.text == "_":
            self.advance()
            return A.WildcardPat(tok.span)
        if tok.kind in ("int", "float", "str", "true", "false", "nil") or (
                tok.kind == "-" and self.peek().kind in ("int", "float")):
            lit = self.literal_pattern_value()
            if self.at("..", "..="):                       # a range: 1..=9
                inclusive = self.advance().kind == "..="
                if not isinstance(lit, A.IntLit):
                    raise ParseError("range patterns work on Ints, e.g. 1..=9", lit.span)
                if not (self.at("int") or (self.at("-") and self.peek().kind == "int")):
                    raise ParseError(f"expected the end of the range, found {self.describe(self.tok)}", self.tok.span)
                end = self.literal_pattern_value()
                return A.RangePat(tok.span.to(end.span), lit.value, end.value, inclusive)
            return A.LitPat(tok.span.to(lit.span), lit)
        if tok.kind == "ident":
            self.advance()
            enum_name = None
            variant = tok
            if self.at(".") and self.peek().kind == "ident":
                self.advance()
                enum_name = tok.text
                variant = self.advance()
            if not variant.text[0].isupper():
                if enum_name is not None:
                    raise ParseError("variant names start with a capital letter", variant.span)
                return A.BindPat(tok.span, tok.text)
            if enum_name is None and self.at("{"):
                return self.struct_pattern(tok)
            args = None
            end = variant.span
            if self.accept("("):
                args = []
                while not self.at(")"):
                    args.append(self.pattern())
                    if not self.accept(","):
                        break
                end = self.expect(")", "')'").span
            return A.VariantPat(tok.span.to(end), enum_name, variant.text, args)
        raise ParseError(f"expected a pattern, found {self.describe(tok)}", tok.span,
                         notes=["patterns: _, a name, a literal (1, \"x\", true, nil), or a variant like Circle(r)"])


def parse(text: str, name: str = "<input>") -> A.Program:
    source = Source(text, name)
    tokens = Lexer(source).tokenize()
    return Parser(tokens, source).parse_program()
