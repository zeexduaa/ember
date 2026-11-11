# Errors as values: operations return a Result-like enum instead of crashing.
struct Account { owner: Str, balance: Int }

enum Outcome {
    Ok(Int)                       # the new balance
    Insufficient(Int)             # how much is missing
    Invalid(Str)
}

impl Account {
    fn deposit(self, amount: Int) -> Outcome {
        if amount <= 0 { return Outcome.Invalid("deposits must be positive") }
        self.balance += amount
        Outcome.Ok(self.balance)
    }

    fn withdraw(self, amount: Int) -> Outcome {
        if amount <= 0 { return Outcome.Invalid("withdrawals must be positive") }
        if amount > self.balance { return Outcome.Insufficient(amount - self.balance) }
        self.balance -= amount
        Outcome.Ok(self.balance)
    }
}

fn report(what: Str, outcome: Outcome) {
    let text = match outcome {
        Ok(balance) => "ok, balance is ${balance}"
        Insufficient(missing) => "refused: ${missing} short"
        Invalid(reason) => "refused: ${reason}"
    }
    print("${what}: ${text}")
}

let acct = Account { owner: "Ada", balance: 100 }
report("deposit 50", acct.deposit(50))
report("withdraw 500", acct.withdraw(500))
report("withdraw -1", acct.withdraw(-1))
report("withdraw 120", acct.withdraw(120))
print("${acct.owner} ends with ${acct.balance}")
