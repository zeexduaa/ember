# Tests for the standard library's math module. Run them with: ember test examples
import "math"

fn test_gcd() {
    assert_eq(math.gcd(84, 36), 12)
    assert_eq(math.gcd(-84, 36), 12, "the sign is dropped")
    assert_eq(math.gcd(7, 0), 7)
}

fn test_lcm() {
    assert_eq(math.lcm(4, 6), 12)
    assert_eq(math.lcm(0, 5), 0)
}

fn test_primes() {
    assert_eq(math.primes_below(20), [2, 3, 5, 7, 11, 13, 17, 19])
    assert(math.is_prime(7919), "7919 is prime")
    assert(not math.is_prime(1), "1 isn't prime")
}

fn test_mean() {
    assert_eq(math.mean([1.0, 2.0, 6.0]), 3.0)
    assert_eq(math.mean([]), nil)
}
