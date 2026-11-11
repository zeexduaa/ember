# A recursive-descent parser and evaluator for arithmetic, written in ember.
enum Token { Num(Float), Op(Str), LParen, RParen }
enum Expr { Lit(Float), Bin(Str, Expr, Expr), Neg(Expr) }

fn tokenize(src: Str) -> List[Token] {
    var tokens: List[Token] = []
    var i = 0
    let chars = src.chars()
    while i < chars.len() {
        let c = chars[i]
        if c == " " {
            i += 1
        } else if "+-*/".contains(c) {
            tokens.push(Token.Op(c))
            i += 1
        } else if c == "(" {
            tokens.push(Token.LParen)
            i += 1
        } else if c == ")" {
            tokens.push(Token.RParen)
            i += 1
        } else {
            var j = i
            while j < chars.len() and "0123456789.".contains(chars[j]) { j += 1 }
            if j == i { panic("unexpected character '${c}'") }
            tokens.push(Token.Num(parse_float(src.slice(i, j)) ?? 0.0))
            i = j
        }
    }
    tokens
}

struct Parser { tokens: List[Token], pos: Int }

impl Parser {
    fn peek(self) -> Token? {
        if self.pos < self.tokens.len() { self.tokens[self.pos] } else { nil }
    }

    fn advance(self) -> Token? {
        let t = self.peek()
        self.pos += 1
        t
    }

    # expr := term (('+' | '-') term)*
    fn expr(self) -> Expr {
        var left = self.term()
        while true {
            match self.peek() {
                Op(op) if op == "+" or op == "-" => {
                    self.advance()
                    left = Expr.Bin(op, left, self.term())
                }
                _ => { break }
            }
        }
        left
    }

    # term := unary (('*' | '/') unary)*
    fn term(self) -> Expr {
        var left = self.unary()
        while true {
            match self.peek() {
                Op(op) if op == "*" or op == "/" => {
                    self.advance()
                    left = Expr.Bin(op, left, self.unary())
                }
                _ => { break }
            }
        }
        left
    }

    fn unary(self) -> Expr {
        match self.peek() {
            Op("-") => {
                self.advance()
                Expr.Neg(self.unary())
            }
            _ => self.atom()
        }
    }

    fn atom(self) -> Expr {
        match self.advance() {
            Num(n) => Expr.Lit(n)
            LParen => {
                let inner = self.expr()
                match self.advance() {
                    RParen => inner
                    _ => panic("expected )")
                }
            }
            _ => panic("expected a number or (")
        }
    }
}

fn eval(e: Expr) -> Float {
    match e {
        Lit(n) => n
        Neg(inner) => -eval(inner)
        Bin(op, a, b) => {
            let x = eval(a)
            let y = eval(b)
            match op {
                "+" => x + y
                "-" => x - y
                "*" => x * y
                _ => x / y
            }
        }
    }
}

fn show(e: Expr) -> Str {
    match e {
        Lit(n) => str(n)
        Neg(inner) => "(-${show(inner)})"
        Bin(op, a, b) => "(${show(a)} ${op} ${show(b)})"
    }
}

for src in ["1 + 2 * 3", "(1 + 2) * 3", "-4 / (2 - 0.5)", "2 * -3 - -1"] {
    let tree = Parser { tokens: tokenize(src), pos: 0 }.expr()
    print("${src} => ${show(tree)} = ${eval(tree)}")
}
