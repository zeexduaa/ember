"""Command-line interface (only --version so far)."""

from __future__ import annotations

import argparse

from . import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ember", description="The ember programming language.")
    parser.add_argument("--version", action="version", version=f"ember {__version__}")
    parser.parse_args(argv)
    parser.print_help()
    return 0
