# Functions are values: closures, composition and a memoized function.
fn counter(start: Int) -> fn() -> Int {
    var n = start
    fn() {
        n += 1
        n
    }
}

fn compose(f: fn(Int) -> Int, g: fn(Int) -> Int) -> fn(Int) -> Int {
    fn(x: Int) -> Int { g(f(x)) }
}

let next = counter(10)
next()
next()
print("counter:", next())

let add_one = fn(x: Int) -> Int { x + 1 }
let double = fn(x: Int) -> Int { x * 2 }
print("compose:", compose(add_one, double)(5), compose(double, add_one)(5))

# Each loop iteration gets its own variable, so each closure remembers its own i.
var greeters: List[fn() -> Str] = []
for i in 1..=3 {
    greeters.push(fn() { "greeter ${i}" })
}
print(greeters.map(fn(g) { g() }).join(", "))

# Memoization with a map that lives in a closure.
fn memo_fib() -> fn(Int) -> Int {
    var cache: Map[Int, Int] = {}
    fn fib(n: Int) -> Int {
        if n < 2 { return n }
        if let known = cache.get(n) { return known }
        let value = fib(n - 1) + fib(n - 2)
        cache[n] = value
        value
    }
    fib
}
let fib = memo_fib()
print("fib(90) =", fib(90))
