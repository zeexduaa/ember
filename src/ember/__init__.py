"""ember: a small statically typed language with a bytecode VM.

The pipeline, one module per stage:

    source --lexer--> tokens --parser--> AST --checker--> typed AST
           --compiler--> bytecode --vm--> result

``ember.run_source`` runs all of it.
"""

__version__ = "0.1.0"
