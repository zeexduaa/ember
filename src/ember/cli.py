"""Command-line interface.

    ember run FILE        check, compile and run a program
    ember check FILE...   report errors and warnings without running
    ember dis FILE        show the bytecode
    ember eval "CODE"     run a snippet
    ember repl            the interactive prompt (also: ember with no arguments)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .driver import compile_file, compile_source, dis_source, make_vm
from .source import EmberError
from .vm import RuntimeFailure


def _report_warnings(compiled, quiet: bool) -> None:
    if compiled.warnings and not quiet:
        print(compiled.render_warnings(), file=sys.stderr)
        print(file=sys.stderr)


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        print(f"ember: no such file: {path}", file=sys.stderr)
    except IsADirectoryError:
        print(f"ember: {path} is a folder, not a file", file=sys.stderr)
    except UnicodeDecodeError:
        print(f"ember: {path} isn't UTF-8 text", file=sys.stderr)
    return None


def cmd_run(args: argparse.Namespace) -> int:
    if _read(args.file) is None:
        return 1
    try:
        compiled = compile_file(args.file)
    except EmberError as exc:
        print(exc.render(), file=sys.stderr)
        return 1
    _report_warnings(compiled, args.quiet)
    try:
        make_vm(compiled).run_main(compiled.proto)
    except RuntimeFailure as exc:
        sys.stdout.flush()
        print(exc.render(), file=sys.stderr)
        return 1
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    failed = 0
    for path in args.files:
        if _read(path) is None:
            failed += 1
            continue
        try:
            compiled = compile_file(path)
        except EmberError as exc:
            print(exc.render(), file=sys.stderr)
            print(file=sys.stderr)
            failed += 1
            continue
        _report_warnings(compiled, args.quiet)
        if len(args.files) > 1 or args.verbose:
            print(f"{path}: ok" + (f" ({len(compiled.warnings)} warning(s))" if compiled.warnings else ""))
    return 1 if failed else 0


def cmd_dis(args: argparse.Namespace) -> int:
    text = _read(args.file)
    if text is None:
        return 1
    try:
        print(dis_source(text, args.file, Path(args.file).resolve()))
    except EmberError as exc:
        print(exc.render(), file=sys.stderr)
        return 1
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    try:
        compiled = compile_source(args.code, "<eval>", Path.cwd() / "<eval>")
    except EmberError as exc:
        print(exc.render(), file=sys.stderr)
        return 1
    try:
        make_vm(compiled).run_main(compiled.proto)
    except RuntimeFailure as exc:
        print(exc.render(), file=sys.stderr)
        return 1
    return 0


def cmd_repl(args: argparse.Namespace) -> int:
    from .repl import Session

    try:
        import readline  # noqa: F401  (line editing and history where available)
    except ImportError:  # pragma: no cover - Windows
        pass
    return Session().loop()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ember", description="The ember programming language.")
    parser.add_argument("--version", action="version", version=f"ember {__version__}")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("run", help="check, compile and run a program")
    p.add_argument("file")
    p.add_argument("-q", "--quiet", action="store_true", help="don't show warnings")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("check", help="report errors and warnings without running")
    p.add_argument("files", nargs="+")
    p.add_argument("-q", "--quiet", action="store_true", help="don't show warnings")
    p.add_argument("-v", "--verbose", action="store_true", help="say 'ok' for files without errors")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("dis", help="show the bytecode a program compiles to")
    p.add_argument("file")
    p.set_defaults(func=cmd_dis)

    p = sub.add_parser("eval", help="run a snippet of code")
    p.add_argument("code")
    p.set_defaults(func=cmd_eval)

    p = sub.add_parser("repl", help="the interactive prompt")
    p.set_defaults(func=cmd_repl)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command is None:
            return cmd_repl(args)
        return args.func(args)
    except BrokenPipeError:
        # The reader went away (e.g. `ember run x.em | head`): stop quietly, like other command-line tools.
        import os
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        return 0
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
