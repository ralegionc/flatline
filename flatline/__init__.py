"""flatline -- a regular expression engine that cannot be made to blow up.

    >>> import flatline
    >>> flatline.search(r"(\\w+)@(\\w+)\\.com", "mail bob@example.com now").groups()
    ('bob', 'example')

Every search runs in time proportional to ``len(text) * len(pattern)``, for
every input, because the matcher simulates the whole NFA at once instead of
backtracking through it.  See ``flatline.pike`` for the mechanism and the
README for why the engines you already use do not have this property.
"""

from .api import (
    Match,
    Pattern,
    RegexSyntaxError,
    compile,
    finditer,
    findall,
    fullmatch,
    match,
    search,
    sub,
)
from .compiler import compile_pattern
from .program import Program

__version__ = "0.1.0"

__all__ = [
    "Pattern",
    "Match",
    "Program",
    "RegexSyntaxError",
    "compile",
    "compile_pattern",
    "search",
    "match",
    "fullmatch",
    "finditer",
    "findall",
    "sub",
    "__version__",
]
