"""The public API: ``compile``, ``search``, ``match``, ``finditer`` and friends.

Deliberately shaped like the standard library's ``re`` so that swapping the
import is a one-line experiment, and so the differential tests can compare the
two engines directly.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Iterator

from .backtrack import DEFAULT_MAX_STEPS, BacktrackResult
from .backtrack import search as bt_search
from .compiler import compile_pattern
from .parser import RegexSyntaxError
from .pike import Run, Saved
from .program import Program

__all__ = [
    "Pattern",
    "Match",
    "RegexSyntaxError",
    "compile",
    "search",
    "match",
    "fullmatch",
    "finditer",
    "findall",
    "sub",
]


class Match:
    """The result of a successful match."""

    __slots__ = ("_pattern", "_text", "_saved")

    def __init__(self, pattern: "Pattern", text: str, saved: Saved) -> None:
        self._pattern = pattern
        self._text = text
        self._saved = saved

    # -- group access

    def _index(self, group: int | str) -> int:
        if isinstance(group, str):
            try:
                return self._pattern.program.groupnames[group]
            except KeyError:
                raise IndexError("no such group: {!r}".format(group)) from None
        if not 0 <= group <= self._pattern.program.ngroups:
            raise IndexError("no such group: {}".format(group))
        return group

    def group(self, group: int | str = 0) -> str | None:
        s, e = self.span(group)
        return None if s < 0 else self._text[s:e]

    def groups(self, default: str | None = None) -> tuple[str | None, ...]:
        return tuple(
            self.group(i) if self.span(i)[0] >= 0 else default
            for i in range(1, self._pattern.program.ngroups + 1)
        )

    def groupdict(self, default: str | None = None) -> dict[str, str | None]:
        out: dict[str, str | None] = {}
        for name, idx in self._pattern.program.groupnames.items():
            out[name] = self.group(idx) if self.span(idx)[0] >= 0 else default
        return out

    def span(self, group: int | str = 0) -> tuple[int, int]:
        i = self._index(group)
        s, e = self._saved[2 * i], self._saved[2 * i + 1]
        if s is None or e is None:
            return (-1, -1)
        return (s, e)

    def start(self, group: int | str = 0) -> int:
        return self.span(group)[0]

    def end(self, group: int | str = 0) -> int:
        return self.span(group)[1]

    @property
    def string(self) -> str:
        return self._text

    @property
    def re(self) -> "Pattern":
        return self._pattern

    def __repr__(self) -> str:
        s, e = self.span()
        return "<flatline.Match span=({}, {}) match={!r}>".format(s, e, self.group())


class Pattern:
    """A compiled pattern."""

    def __init__(self, pattern: str, flags: str = "") -> None:
        self.pattern = pattern
        self.flags = flags
        self.program: Program = compile_pattern(pattern, flags)
        self._full_program: Program | None = None

    @property
    def full_program(self) -> Program:
        if self._full_program is None:
            self._full_program = compile_pattern(self.pattern, self.flags, full=True)
        return self._full_program

    @property
    def groups(self) -> int:
        return self.program.ngroups

    @property
    def groupindex(self) -> dict[str, int]:
        return dict(self.program.groupnames)

    # -- matching

    def search(self, text: str, pos: int = 0) -> Match | None:
        saved = Run(self.program, text).search(pos, anchored=False)
        return None if saved is None else Match(self, text, saved)

    def match(self, text: str, pos: int = 0) -> Match | None:
        saved = Run(self.program, text).search(pos, anchored=True)
        return None if saved is None else Match(self, text, saved)

    def fullmatch(self, text: str, pos: int = 0) -> Match | None:
        saved = Run(self.full_program, text).search(pos, anchored=True)
        return None if saved is None else Match(self, text, saved)

    def finditer(self, text: str) -> Iterator[Match]:
        pos = 0
        while pos <= len(text):
            m = self.search(text, pos)
            if m is None:
                return
            yield m
            s, e = m.span()
            # An empty match would otherwise spin forever on the same offset.
            pos = e + 1 if e == s else e

    def findall(self, text: str) -> list:
        out = []
        for m in self.finditer(text):
            if self.groups == 0:
                out.append(m.group())
            elif self.groups == 1:
                out.append(m.group(1))
            else:
                out.append(m.groups())
        return out

    def sub(self, repl: str, text: str, count: int = 0) -> str:
        pieces: list[str] = []
        last = 0
        n = 0
        for m in self.finditer(text):
            if count and n >= count:
                break
            s, e = m.span()
            pieces.append(text[last:s])
            pieces.append(repl)
            last = e
            n += 1
        pieces.append(text[last:])
        return "".join(pieces)

    def split(self, text: str, maxsplit: int = 0) -> list[str]:
        out: list[str] = []
        last = 0
        n = 0
        for m in self.finditer(text):
            s, e = m.span()
            if s == e:
                continue  # a zero-width split point would produce empty noise
            if maxsplit and n >= maxsplit:
                break
            out.append(text[last:s])
            last = e
            n += 1
        out.append(text[last:])
        return out

    # -- instrumentation

    def stats(self, text: str, pos: int = 0) -> dict:
        """Run a search and report the work it cost.

        ``steps`` counts thread advances, ``max_threads`` the widest the thread
        list ever got.  The latter is bounded by the program length, which is
        the guarantee made visible.
        """
        run = Run(self.program, text)
        saved = run.search(pos, anchored=False)
        return {
            "matched": saved is not None,
            "steps": run.steps,
            "max_threads": run.max_threads,
            "program_length": len(self.program),
            "text_length": len(text),
        }

    def backtrack(
        self, text: str, pos: int = 0, max_steps: int = DEFAULT_MAX_STEPS
    ) -> BacktrackResult:
        """Run the naive backtracker over the same program, for comparison."""
        return bt_search(self.program, text, pos, anchored=False, max_steps=max_steps)

    def __repr__(self) -> str:
        flags = ", flags={!r}".format(self.flags) if self.flags else ""
        return "flatline.compile({!r}{})".format(self.pattern, flags)


@lru_cache(maxsize=256)
def _cached(pattern: str, flags: str) -> Pattern:
    return Pattern(pattern, flags)


def compile(pattern: str, flags: str = "") -> Pattern:  # noqa: A001 - mirrors re.compile
    """Compile ``pattern`` into a reusable :class:`Pattern`."""
    return _cached(pattern, flags)


def search(pattern: str, text: str, flags: str = "") -> Match | None:
    return compile(pattern, flags).search(text)


def match(pattern: str, text: str, flags: str = "") -> Match | None:
    return compile(pattern, flags).match(text)


def fullmatch(pattern: str, text: str, flags: str = "") -> Match | None:
    return compile(pattern, flags).fullmatch(text)


def finditer(pattern: str, text: str, flags: str = "") -> Iterator[Match]:
    return compile(pattern, flags).finditer(text)


def findall(pattern: str, text: str, flags: str = "") -> list:
    return compile(pattern, flags).findall(text)


def sub(pattern: str, repl: str, text: str, count: int = 0, flags: str = "") -> str:
    return compile(pattern, flags).sub(repl, text, count)
