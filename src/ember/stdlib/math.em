# Number helpers. import "math"

pub let PI = 3.141592653589793
pub let E = 2.718281828459045

# The greatest common divisor of a and b (always >= 0).
pub fn gcd(a: Int, b: Int) -> Int {
    var x = a.abs()
    var y = b.abs()
    while y != 0 {
        let t = x % y
        x = y
        y = t
    }
    x
}

# The least common multiple of a and b (0 if either is 0).
pub fn lcm(a: Int, b: Int) -> Int {
    if a == 0 or b == 0 { return 0 }
    (a / gcd(a, b) * b).abs()
}

pub fn clamp(x: Float, low: Float, high: Float) -> Float {
    if x < low { low } else if x > high { high } else { x }
}

pub fn clamp_int(x: Int, low: Int, high: Int) -> Int {
    if x < low { low } else if x > high { high } else { x }
}

pub fn is_prime(n: Int) -> Bool {
    if n < 2 { return false }
    if n % 2 == 0 { return n == 2 }
    var d = 3
    while d * d <= n {
        if n % d == 0 { return false }
        d += 2
    }
    true
}

# The primes below n (sieve of Eratosthenes).
pub fn primes_below(n: Int) -> List[Int] {
    if n < 3 { return [] }
    var sieve: List[Bool] = []
    for _ in 0..n { sieve.push(true) }
    sieve[0] = false
    sieve[1] = false
    var i = 2
    while i * i < n {
        if sieve[i] {
            var j = i * i
            while j < n {
                sieve[j] = false
                j += i
            }
        }
        i += 1
    }
    var out: List[Int] = []
    for k in 0..n {
        if sieve[k] { out.push(k) }
    }
    out
}

pub fn factorial(n: Int) -> Int {
    assert(n >= 0, "factorial of a negative number")
    var result = 1
    for k in 2..=n { result *= k }
    result
}

pub fn mean(xs: List[Float]) -> Float? {
    if xs.is_empty() { return nil }
    xs.sum() / float(xs.len())
}
