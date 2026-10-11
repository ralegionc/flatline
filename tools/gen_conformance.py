"""Record what the Python engine does, so the JavaScript port can be held to it.

Run from the repository root::

    python tools/gen_conformance.py

This writes ``web/conformance.js``.  Two things consume it: the page runs the
corpus on load and shows the result in its header, and ``tests/test_js_port.py``
runs the same corpus under Node and fails the build on any disagreement.

The corpus records the *compiled program* as well as the match result.  Two
engines can agree on every answer while disagreeing about how they got there,
and since the visualiser draws the program rather than the answer, a drift in
the compiler would put a wrong picture on the screen with every test still
green.
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from flatline.api import Pattern  # noqa: E402
from flatline.compiler import compile_pattern  # noqa: E402
from flatline.parser import RegexSyntaxError  # noqa: E402

OUT = pathlib.Path(__file__).resolve().parents[1] / "web" / "conformance.js"

# (pattern, text, flags)
CASES: list[tuple[str, str, str]] = [
    # literals and the basics
    ("a", "a", ""),
    ("a", "bca", ""),
    ("abc", "xxabcxx", ""),
    ("a", "b", ""),
    ("", "abc", ""),
    (".", "x", ""),
    (".", "\n", ""),
    (".", "\n", "s"),
    # alternation and priority
    ("a|b", "b", ""),
    ("a|ab", "ab", ""),
    ("ab|a", "ab", ""),
    ("(foo|foobar)baz", "foobarbaz", ""),
    ("a|b|c|d", "zzd", ""),
    # quantifiers
    ("a*", "", ""),
    ("a*", "aaa", ""),
    ("a+", "", ""),
    ("a+", "aaa", ""),
    ("a?", "", ""),
    ("ab?c", "ac", ""),
    ("ab?c", "abc", ""),
    ("a*?", "aaa", ""),
    ("a+?b", "aaab", ""),
    ("<.*>", "<a><b>", ""),
    ("<.*?>", "<a><b>", ""),
    ("a{3}", "aaaa", ""),
    ("a{3}", "aa", ""),
    ("a{2,4}", "aaaaa", ""),
    ("a{2,}", "aaaaa", ""),
    ("a{0,2}", "aaa", ""),
    ("a{2,4}?", "aaaa", ""),
    ("(ab){2}", "ababab", ""),
    # groups and captures
    (r"(\d+)-(\d+)", "tel 555-1234 x", ""),
    (r"(a)(b)?c", "ac", ""),
    (r"((a)(b))c", "abc", ""),
    (r"(?:(\w)-)+", "a-b-c-", ""),
    (r"(?P<year>\d{4})-(?P<month>\d{2})", "on 2024-03-19", ""),
    (r"(?:ab)+", "ababab", ""),
    # classes
    ("[abc]+", "xxabcaxx", ""),
    ("[^abc]+", "abxyzab", ""),
    ("[a-z]+", "ABCdefGHI", ""),
    ("[]]", "]", ""),
    ("[-a]+", "-a-", ""),
    ("[a-]+", "a-", ""),
    (r"[\d]+", "ab123", ""),
    (r"[\w.]+", " a.b ", ""),
    (r"[^\d]+", "12ab34", ""),
    (r"[\d\s]+", "a1 2b", ""),
    # anchors and boundaries
    ("^abc$", "abc", ""),
    ("^abc$", "xabc", ""),
    ("abc$", "abc\n", ""),
    (r"abc\Z", "abc\n", ""),
    (r"abc\Z", "abc", ""),
    (r"\bcat\b", "the cat sat", ""),
    (r"\bcat\b", "concatenate", ""),
    (r"\Bcat\B", "concatenate", ""),
    (r"^\w+", "one\ntwo", "m"),
    (r"\w+$", "one\ntwo", "m"),
    # flags
    ("hello", "HELLO", "i"),
    ("[a-f]+", "ABCXYZ", "i"),
    ("hello", "HELLO", ""),
    # realistic
    (r"\w+@\w+\.\w+", "contact bob@example.com today", ""),
    (r"(\d{4})-(\d{2})-(\d{2})", "due 2024-03-19 sharp", ""),
    (r"https?://[\w./-]+", "see http://example.com/x for more", ""),
    (r"(?:\d{1,3}\.){3}\d{1,3}", "ip 192.168.0.1 here", ""),
    (r"[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", "value -1.5e10 end", ""),
    (r"^(?:GET|POST) (/\S*) HTTP/1\.[01]$", "GET /index.html HTTP/1.1", ""),
    (r"'[^']*'", "say 'hello there' now", ""),
    # the pathological ones, on inputs small enough for both engines
    ("(a+)+b", "aaaaaaaa!", ""),
    ("(a+)+b", "aaaaaaaab", ""),
    ("(a|a)*b", "aaaaaa!", ""),
    ("(a*)*b", "aaab", ""),
    ("(x+x+)+y", "xxxxxxx!", ""),
    ("(a|aa)+c", "aaaaaa!", ""),
    (r"^(\w+\s?)*$", "aaaaaaa!", ""),
]

#: Patterns that must be refused, and the phrase the message has to contain.
REFUSALS: list[tuple[str, str]] = [
    (r"(a)\1", "backreference"),
    (r"(?P<x>a)\k<x>", "backreference"),
    ("a(?=b)", "not implemented"),
    ("(?<=a)b", "not implemented"),
    ("(a", "missing ')'"),
    ("a)", "unbalanced"),
    ("[a", "unterminated character class"),
    ("*a", "nothing to repeat"),
    ("a\\", "trailing backslash"),
    (r"\q", "unknown escape"),
    ("[z-a]", "out of order"),
    ("a{5,2}", "max below min"),
]


def build() -> dict:
    cases = []
    for pattern, text, flags in CASES:
        p = Pattern(pattern, flags)
        m = p.search(text)
        prog = compile_pattern(pattern, flags)
        bt = p.backtrack(text, max_steps=2_000_000)

        cases.append(
            {
                "pattern": pattern,
                "text": text,
                "flags": flags,
                "program": [inst.render() for inst in prog.insts],
                "matched": m is not None,
                "spans": [list(m.span(i)) for i in range(p.groups + 1)] if m else [],
                "pike_steps": p.stats(text)["steps"],
                "pike_max_threads": p.stats(text)["max_threads"],
                "backtrack_steps": bt.steps,
                "backtrack_gave_up": bt.gave_up,
            }
        )

    refusals = []
    for pattern, fragment in REFUSALS:
        try:
            compile_pattern(pattern)
        except RegexSyntaxError as exc:
            refusals.append({"pattern": pattern, "fragment": fragment, "pos": exc.pos})
        else:  # pragma: no cover - would mean the corpus is stale
            raise SystemExit("expected {!r} to be refused".format(pattern))

    return {"cases": cases, "refusals": refusals}


def main() -> None:
    data = build()
    body = json.dumps(data, indent=1, ensure_ascii=False)
    OUT.write_text(
        "/* Generated by tools/gen_conformance.py -- do not edit by hand.\n"
        " *\n"
        " * What the Python engine does, recorded so the JavaScript port can be\n"
        " * checked against it: in the browser on page load, and under Node in\n"
        " * tests/test_js_port.py.\n"
        " */\n"
        "const CONFORMANCE = " + body + ";\n"
        "if (typeof module !== \"undefined\" && module.exports) module.exports = CONFORMANCE;\n"
        "if (typeof window !== \"undefined\") window.CONFORMANCE = CONFORMANCE;\n",
        encoding="utf-8",
    )
    print(
        "wrote {} ({} cases, {} refusals)".format(
            OUT.relative_to(pathlib.Path.cwd()) if OUT.is_relative_to(pathlib.Path.cwd()) else OUT,
            len(data["cases"]),
            len(data["refusals"]),
        )
    )


if __name__ == "__main__":
    main()
