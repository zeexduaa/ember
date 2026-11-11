# Contributing

Thanks for looking! A few things keep ember easy to work on.

- **No dependencies.** The implementation uses only Python's standard library. pytest is just for the tests.
- **Every stage gets tests.** Lexer, parser, checker and VM each have their own test file. A bug fix comes with a test that fails without it.
- **Errors are part of the design.** A new error message says what's wrong, points at the right span, and adds a note when the fix isn't obvious. Look at `checker.py` for the tone.
- **Docs follow the code.** A new feature is described in [docs/language.md](docs/language.md). If it changes how a stage works, update [docs/internals.md](docs/internals.md) too.

## Setup

```console
$ git clone https://github.com/zeexduaa/ember
$ cd ember
$ python -m pip install -e . pytest
$ python -m pytest
```

## Where to start

[docs/internals.md](docs/internals.md) walks through the pipeline, and the [roadmap](ROADMAP.md) lists what's planned, with rough sizes. The **S** items are good first issues.

## Adding an example

Put `name.em` in `examples/`, run `ember run -q examples/name.em > examples/name.out`, and check the output by hand. `tests/test_examples.py` then keeps it working. Examples must compile without warnings.
