"""Instruction set for the flatline virtual machine.

A compiled pattern is a flat list of instructions. Both the Pike VM
(``flatline.pike``) and the deliberately naive backtracker
(``flatline.backtrack``) execute this same program, which is what makes the
comparison between them honest: the only difference is the execution
strategy, not the compiler.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

# --- rendering --------------------------------------------------------------
#
# Instruction text is compared byte-for-byte against the JavaScript port (see
# tools/gen_conformance.py), so the escaping rules below are part of the
# contract between the two implementations rather than a cosmetic choice.
# Python's own ``repr`` is not usable here: it switches quote style depending
# on the content, which JavaScript would have to reimplement exactly.

_ESCAPES = {"\\": "\\\\", "\n": "\\n", "\t": "\\t", "\r": "\\r", "\f": "\\f", "\v": "\\v"}


def esc(cp: int) -> str:
    """Escape one codepoint for display inside a character class."""
    ch = chr(cp)
    if ch in _ESCAPES:
        return _ESCAPES[ch]
    if ch in "]-^":
        return "\\" + ch
    if cp < 32 or cp == 127:
        return "\\x{:02x}".format(cp)
    if cp > 126:
        return "\\u{:04x}".format(cp)
    return ch


def quote_char(ch: str) -> str:
    """Render a literal character the same way the JavaScript port does."""
    cp = ord(ch)
    if ch == "'":
        return "\\'"
    if ch in _ESCAPES:
        return _ESCAPES[ch]
    if cp < 32 or cp == 127:
        return "\\x{:02x}".format(cp)
    if cp > 126:
        return "\\u{:04x}".format(cp)
    return ch


# --- character classes ------------------------------------------------------


@dataclass(frozen=True)
class CharClass:
    """A set of codepoints, stored as sorted inclusive ranges."""

    ranges: tuple[tuple[int, int], ...]
    negated: bool = False

    @staticmethod
    def from_ranges(ranges: Iterable[tuple[int, int]], negated: bool = False) -> "CharClass":
        merged: list[tuple[int, int]] = []
        for lo, hi in sorted(ranges):
            if merged and lo <= merged[-1][1] + 1:
                prev_lo, prev_hi = merged[-1]
                merged[-1] = (prev_lo, max(prev_hi, hi))
            else:
                merged.append((lo, hi))
        return CharClass(tuple(merged), negated)

    def matches(self, ch: str) -> bool:
        cp = ord(ch)
        lo_i, hi_i = 0, len(self.ranges) - 1
        found = False
        while lo_i <= hi_i:
            mid = (lo_i + hi_i) // 2
            lo, hi = self.ranges[mid]
            if cp < lo:
                hi_i = mid - 1
            elif cp > hi:
                lo_i = mid + 1
            else:
                found = True
                break
        return found != self.negated

    def describe(self) -> str:
        parts = []
        for lo, hi in self.ranges:
            if lo == hi:
                parts.append(esc(lo))
            elif hi == lo + 1:
                parts.append(esc(lo) + esc(hi))
            else:
                parts.append("{}-{}".format(esc(lo), esc(hi)))
        return "[" + ("^" if self.negated else "") + "".join(parts) + "]"


# --- instructions -----------------------------------------------------------

# Assertion kinds recognised by the ASSERT instruction.
BOL = "bol"            # ^
EOL = "eol"            # $
WORD_B = "wordb"       # \b
NOT_WORD_B = "nwordb"  # \B
BOS = "bos"            # \A
EOS = "eos"            # \Z

CHAR = "char"
CLASS = "class"
ANY = "any"
MATCH = "match"
JMP = "jmp"
SPLIT = "split"
SAVE = "save"
ASSERT = "assert"

#: Instructions that consume exactly one input character.
CONSUMING = frozenset({CHAR, CLASS, ANY})


@dataclass
class Inst:
    op: str
    #: literal character for CHAR
    ch: str | None = None
    #: character set for CLASS
    cls: CharClass | None = None
    #: jump targets for JMP (``x``) and SPLIT (``x`` preferred, ``y`` fallback)
    x: int = 0
    y: int = 0
    #: capture slot for SAVE
    slot: int = 0
    #: assertion kind for ASSERT
    kind: str = ""
    #: whether ANY also matches a newline (the ``s`` flag)
    dotall: bool = False

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<{}>".format(self.render())

    def render(self) -> str:
        if self.op == CHAR:
            assert self.ch is not None
            return "char '{}'".format(quote_char(self.ch))
        if self.op == CLASS:
            assert self.cls is not None
            return "class {}".format(self.cls.describe())
        if self.op == ANY:
            return "any" + (" (dotall)" if self.dotall else "")
        if self.op == MATCH:
            return "match"
        if self.op == JMP:
            return "jmp {}".format(self.x)
        if self.op == SPLIT:
            return "split {}, {}".format(self.x, self.y)
        if self.op == SAVE:
            return "save {}".format(self.slot)
        if self.op == ASSERT:
            return "assert {}".format(self.kind)
        raise AssertionError("unknown op {!r}".format(self.op))


@dataclass
class Program:
    insts: list[Inst]
    #: number of capture groups, *not* counting group 0
    ngroups: int
    #: the pattern this was compiled from, kept for error messages and the UI
    pattern: str = ""
    #: names for named groups, mapping name -> group number
    groupnames: dict[str, int] = field(default_factory=dict)
    flags: frozenset[str] = field(default_factory=frozenset)

    @property
    def nslots(self) -> int:
        """Total capture slots, two per group including the implicit group 0."""
        return 2 * (self.ngroups + 1)

    def __len__(self) -> int:
        return len(self.insts)

    def __getitem__(self, i: int) -> Inst:
        return self.insts[i]

    def disassemble(self) -> str:
        width = max(2, len(str(max(len(self.insts) - 1, 0))))
        return "\n".join(
            "{:>{w}}  {}".format(i, inst.render(), w=width)
            for i, inst in enumerate(self.insts)
        )
