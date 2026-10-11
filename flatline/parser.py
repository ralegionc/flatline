"""Recursive-descent parser: pattern text -> abstract syntax tree.

Grammar, loosest binding first::

    alt     := concat ('|' concat)*
    concat  := repeat*
    repeat  := atom (('*' | '+' | '?' | '{m,n}') '?'?)*
    atom    := '(' group_head alt ')' | '[' class ']' | '.' | '^' | '$'
             | escape | literal

Two constructs are refused on purpose rather than left unimplemented:
backreferences and lookaround.  A backreference makes a pattern non-regular,
and no NFA simulation can evaluate one in linear time -- accepting them would
mean quietly giving up the guarantee this engine exists to make.  See
``UNSUPPORTED`` below and the README section "What it will not do".
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .program import CharClass

#: Upper bound on ``{m,n}`` expansion.  Counted repetition is compiled by
#: copying the body, so an unbounded count would let a short pattern produce
#: an enormous program.
MAX_REPEAT = 1000


class RegexSyntaxError(ValueError):
    """A pattern that could not be parsed, with the offending offset."""

    def __init__(self, message: str, pattern: str, pos: int) -> None:
        self.message = message
        self.pattern = pattern
        self.pos = pos
        super().__init__(self.__str__())

    def __str__(self) -> str:
        caret = " " * self.pos + "^"
        return "{}\n    {}\n    {}\n  at offset {}".format(
            self.message, self.pattern, caret, self.pos
        )


# --- AST --------------------------------------------------------------------


@dataclass
class Node:
    pass


@dataclass
class Empty(Node):
    """Matches the empty string.  Produced by e.g. ``a|`` or ``()``."""


@dataclass
class Literal(Node):
    ch: str


@dataclass
class ClassNode(Node):
    cls: CharClass


@dataclass
class AnyNode(Node):
    """``.`` -- any character, excluding newline unless the ``s`` flag is set."""


@dataclass
class Assertion(Node):
    kind: str


@dataclass
class Concat(Node):
    parts: list[Node] = field(default_factory=list)


@dataclass
class Alternate(Node):
    branches: list[Node] = field(default_factory=list)


@dataclass
class Repeat(Node):
    body: Node
    lo: int
    hi: int | None  # None means unbounded
    greedy: bool = True


@dataclass
class Group(Node):
    body: Node
    #: capture index, or ``None`` for a non-capturing ``(?:...)`` group
    index: int | None = None
    name: str | None = None


# --- shorthand classes ------------------------------------------------------

DIGIT = CharClass.from_ranges([(ord("0"), ord("9"))])
NOT_DIGIT = CharClass.from_ranges(DIGIT.ranges, negated=True)
WORD = CharClass.from_ranges(
    [(ord("0"), ord("9")), (ord("A"), ord("Z")), (ord("_"), ord("_")), (ord("a"), ord("z"))]
)
NOT_WORD = CharClass.from_ranges(WORD.ranges, negated=True)
SPACE = CharClass.from_ranges([(ord(c), ord(c)) for c in " \t\n\r\f\v"])
NOT_SPACE = CharClass.from_ranges(SPACE.ranges, negated=True)

_CLASS_ESCAPES = {
    "d": DIGIT,
    "D": NOT_DIGIT,
    "w": WORD,
    "W": NOT_WORD,
    "s": SPACE,
    "S": NOT_SPACE,
}

_CHAR_ESCAPES = {
    "n": "\n",
    "t": "\t",
    "r": "\r",
    "f": "\f",
    "v": "\v",
    "0": "\0",
    "a": "\a",
    "e": "\x1b",
}

_ASSERT_ESCAPES = {"b": "wordb", "B": "nwordb", "A": "bos", "Z": "eos"}

UNSUPPORTED = {
    "backreference": (
        "backreferences are not supported: a pattern with a backreference is "
        "not a regular language, and cannot be matched in linear time by any "
        "NFA simulation. Supporting it would mean giving up flatline's whole "
        "guarantee."
    ),
    "lookaround": (
        "lookahead and lookbehind are not supported yet. Unlike backreferences "
        "these are compatible with linear-time matching, they are simply not "
        "implemented."
    ),
}


class Parser:
    def __init__(self, pattern: str) -> None:
        self.src = pattern
        self.pos = 0
        self.ngroups = 0
        self.groupnames: dict[str, int] = {}

    # -- helpers

    def error(self, message: str, pos: int | None = None) -> RegexSyntaxError:
        return RegexSyntaxError(message, self.src, self.pos if pos is None else pos)

    def eof(self) -> bool:
        return self.pos >= len(self.src)

    def peek(self) -> str | None:
        return None if self.eof() else self.src[self.pos]

    def next(self) -> str:
        if self.eof():
            raise self.error("unexpected end of pattern")
        ch = self.src[self.pos]
        self.pos += 1
        return ch

    def eat(self, ch: str) -> bool:
        if self.peek() == ch:
            self.pos += 1
            return True
        return False

    # -- grammar

    def parse(self) -> Node:
        node = self.parse_alt()
        if not self.eof():
            raise self.error("unbalanced ')'")
        return node

    def parse_alt(self) -> Node:
        branches = [self.parse_concat()]
        while self.eat("|"):
            branches.append(self.parse_concat())
        return branches[0] if len(branches) == 1 else Alternate(branches)

    def parse_concat(self) -> Node:
        parts: list[Node] = []
        while not self.eof() and self.peek() not in ("|", ")"):
            parts.append(self.parse_repeat())
        if not parts:
            return Empty()
        return parts[0] if len(parts) == 1 else Concat(parts)

    def parse_repeat(self) -> Node:
        node = self.parse_atom()
        while True:
            ch = self.peek()
            start = self.pos
            if ch == "*":
                self.pos += 1
                lo, hi = 0, None
            elif ch == "+":
                self.pos += 1
                lo, hi = 1, None
            elif ch == "?":
                self.pos += 1
                lo, hi = 0, 1
            elif ch == "{":
                bounds = self.try_parse_counted()
                if bounds is None:
                    break  # a literal '{', already handled by parse_atom next round
                lo, hi = bounds
            else:
                break

            if isinstance(node, (Assertion,)):
                raise self.error("nothing to repeat: cannot quantify an assertion", start)
            greedy = not self.eat("?")
            node = Repeat(node, lo, hi, greedy)
        return node

    def try_parse_counted(self) -> tuple[int, int | None] | None:
        """Parse ``{m}``, ``{m,}`` or ``{m,n}``.  Returns ``None`` for a literal brace."""
        save = self.pos
        self.pos += 1  # consume '{'
        digits_lo = self.read_digits()
        if digits_lo is None:
            self.pos = save
            return None
        if self.eat("}"):
            hi: int | None = digits_lo
            lo = digits_lo
        elif self.eat(","):
            lo = digits_lo
            digits_hi = self.read_digits()
            if not self.eat("}"):
                self.pos = save
                return None
            hi = digits_hi
        else:
            self.pos = save
            return None

        if hi is not None and hi < lo:
            raise self.error(
                "bad repeat bounds: {{{},{}}} has max below min".format(lo, hi), save
            )
        limit = hi if hi is not None else lo
        if limit > MAX_REPEAT:
            raise self.error(
                "repeat count {} exceeds the limit of {}".format(limit, MAX_REPEAT), save
            )
        return lo, hi

    def read_digits(self) -> int | None:
        start = self.pos
        while not self.eof() and self.src[self.pos].isdigit():
            self.pos += 1
        if self.pos == start:
            return None
        return int(self.src[start : self.pos])

    def parse_atom(self) -> Node:
        start = self.pos
        ch = self.next()

        if ch == "(":
            return self.parse_group(start)
        if ch == "[":
            return ClassNode(self.parse_class(start))
        if ch == ".":
            return AnyNode()
        if ch == "^":
            return Assertion("bol")
        if ch == "$":
            return Assertion("eol")
        if ch == "\\":
            return self.parse_escape(start)
        if ch in ("*", "+", "?"):
            raise self.error("nothing to repeat", start)
        return Literal(ch)

    def parse_group(self, start: int) -> Node:
        name: str | None = None
        capturing = True

        if self.peek() == "?":
            self.pos += 1
            kind = self.peek()
            if kind == ":":
                self.pos += 1
                capturing = False
            elif kind in ("=", "!"):
                raise self.error(UNSUPPORTED["lookaround"], start)
            elif kind == "<":
                after = self.src[self.pos + 1 : self.pos + 2]
                if after in ("=", "!"):
                    raise self.error(UNSUPPORTED["lookaround"], start)
                self.pos += 1
                name = self.read_groupname(start)
            elif kind == "P":
                self.pos += 1
                if not self.eat("<"):
                    raise self.error("expected '<' after '(?P'", start)
                name = self.read_groupname(start)
            else:
                raise self.error("unsupported group syntax '(?{}'".format(kind or ""), start)

        index: int | None = None
        if capturing:
            self.ngroups += 1
            index = self.ngroups
            if name is not None:
                if name in self.groupnames:
                    raise self.error("duplicate group name {!r}".format(name), start)
                self.groupnames[name] = index

        body = self.parse_alt()
        if not self.eat(")"):
            raise self.error("missing ')' for group opened here", start)
        return Group(body, index, name)

    def read_groupname(self, start: int) -> str:
        chars: list[str] = []
        while not self.eof() and self.peek() != ">":
            chars.append(self.next())
        if not self.eat(">"):
            raise self.error("unterminated group name", start)
        name = "".join(chars)
        if not name.isidentifier():
            raise self.error("invalid group name {!r}".format(name), start)
        return name

    def parse_escape(self, start: int) -> Node:
        if self.eof():
            raise self.error("pattern ends with a trailing backslash", start)
        ch = self.next()

        if ch in _CLASS_ESCAPES:
            return ClassNode(_CLASS_ESCAPES[ch])
        if ch in _ASSERT_ESCAPES:
            return Assertion(_ASSERT_ESCAPES[ch])
        if ch.isdigit() and ch != "0":
            raise self.error(UNSUPPORTED["backreference"], start)
        if ch == "k":
            raise self.error(UNSUPPORTED["backreference"], start)
        return Literal(self.escape_char(ch, start))

    def escape_char(self, ch: str, start: int) -> str:
        if ch in _CHAR_ESCAPES:
            return _CHAR_ESCAPES[ch]
        if ch == "x":
            return self.read_hex(2, start)
        if ch == "u":
            return self.read_hex(4, start)
        if ch == "U":
            return self.read_hex(8, start)
        if ch.isalnum():
            raise self.error("unknown escape '\\{}'".format(ch), start)
        return ch  # \. \* \\ \[ ...

    def read_hex(self, n: int, start: int) -> str:
        digits = self.src[self.pos : self.pos + n]
        if len(digits) < n or any(c not in "0123456789abcdefABCDEF" for c in digits):
            raise self.error("expected {} hex digits in escape".format(n), start)
        self.pos += n
        return chr(int(digits, 16))

    def parse_class(self, start: int) -> CharClass:
        negated = self.eat("^")
        ranges: list[tuple[int, int]] = []
        first = True

        while True:
            if self.eof():
                raise self.error("unterminated character class opened here", start)
            if self.peek() == "]" and not first:
                self.pos += 1
                break
            first = False

            lo_pos = self.pos
            ch = self.next()

            if ch == "\\":
                if self.eof():
                    raise self.error("trailing backslash in character class", lo_pos)
                esc = self.next()
                if esc in _CLASS_ESCAPES:
                    sub = _CLASS_ESCAPES[esc]
                    if sub.negated:
                        # e.g. [\D] -- fold by taking the complement of the ranges
                        ranges.extend(_complement(sub.ranges))
                    else:
                        ranges.extend(sub.ranges)
                    continue
                lo_char = self.escape_char(esc, lo_pos)
            else:
                lo_char = ch

            # a range, unless the '-' is the last character before ']'
            if self.peek() == "-" and self.src[self.pos + 1 : self.pos + 2] not in ("]", ""):
                self.pos += 1
                hi_pos = self.pos
                hi_raw = self.next()
                if hi_raw == "\\":
                    esc = self.next()
                    if esc in _CLASS_ESCAPES:
                        raise self.error(
                            "a shorthand class cannot be the end of a range", hi_pos
                        )
                    hi_char = self.escape_char(esc, hi_pos)
                else:
                    hi_char = hi_raw
                if ord(hi_char) < ord(lo_char):
                    raise self.error(
                        "range {}-{} is out of order".format(lo_char, hi_char), lo_pos
                    )
                ranges.append((ord(lo_char), ord(hi_char)))
            else:
                ranges.append((ord(lo_char), ord(lo_char)))

        if not ranges:
            raise self.error("empty character class", start)
        return CharClass.from_ranges(ranges, negated)


def _complement(ranges: tuple[tuple[int, int], ...]) -> list[tuple[int, int]]:
    """Ranges covering everything the input does not, over the Unicode space."""
    out: list[tuple[int, int]] = []
    prev = 0
    for lo, hi in ranges:
        if lo > prev:
            out.append((prev, lo - 1))
        prev = hi + 1
    if prev <= 0x10FFFF:
        out.append((prev, 0x10FFFF))
    return out


def parse(pattern: str) -> tuple[Node, int, dict[str, int]]:
    """Parse ``pattern``, returning ``(ast, group_count, group_names)``."""
    p = Parser(pattern)
    ast = p.parse()
    return ast, p.ngroups, p.groupnames
