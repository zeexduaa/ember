# FizzBuzz with match on a computed value.
fn fizzbuzz(n: Int) -> Str {
    match [n % 3 == 0, n % 5 == 0] {
        x if x[0] and x[1] => "FizzBuzz"
        x if x[0] => "Fizz"
        x if x[1] => "Buzz"
        _ => str(n)
    }
}

var line: List[Str] = []
for i in 1..=15 {
    line.push(fizzbuzz(i))
}
print(line.join(" "))
