"""Parsing: what is accepted, what is refused, and whether the refusal is useful."""

from __future__ import annotations

import pytest

from flatline import compile_pattern
from flatline.parser import MAX_REPEAT, RegexSyntaxError, parse


def groups_of(pattern: str) -> int:
    return parse(pattern)[1]


class TestAccepts:
    @pytest.mark.parametrize(
        "pattern",
        [
            "",
            "a",
            "abc",
            "a|b",
            "a|",
            "|a",
            "()",
            "(a)",
            "(?:a)",
            "(?P<name>a)",
            "(?<name>a)",
            "a*",
            "a+",
            "a?",
            "a*?",
            "a+?",
            "a??",
            "a{3}",
            "a{2,}",
            "a{2,5}",
            "a{0,0}",
            ".",
            "^a$",
            r"\d\w\s\D\W\S",
            r"\b\B\A\Z",
            r"\.\*\+\?\(\)\[\]\{\}\|\\",
            r"\n\t\r\f\v\0",
            r"\x41",
            r"A",
            "[abc]",
            "[a-z]",
            "[^a-z]",
            "[a-zA-Z0-9_]",
            "[]]",
            "[-a]",
            "[a-]",
            r"[\d\s]",
            r"[\]]",
            "((a)(b))",
            "a{2}{3}",
            "(a|b)*c",
        ],
    )
    def test_parses(self, pattern: str) -> None:
        compile_pattern(pattern)

    def test_group_numbering(self) -> None:
        assert groups_of("(a)(b)(c)") == 3
        assert groups_of("((a))") == 2
        assert groups_of("(?:a)(b)") == 1
        assert groups_of("(?:a)(?:b)") == 0

    def test_group_names(self) -> None:
        prog = compile_pattern("(?P<year>a)(b)(?P<rest>c)")
        assert prog.groupnames == {"year": 1, "rest": 3}

    def test_literal_brace_is_not_a_quantifier(self) -> None:
        # '{' that is not a well-formed count is an ordinary character, which
        # is what every mainstream engine does.
        compile_pattern("a{")
        compile_pattern("a{,}")
        compile_pattern("a{x}")


class TestRejects:
    @pytest.mark.parametrize(
        "pattern,fragment",
        [
            ("(a", "missing ')'"),
            ("a)", "unbalanced"),
            ("[a", "unterminated character class"),
            ("*a", "nothing to repeat"),
            ("+", "nothing to repeat"),
            ("?", "nothing to repeat"),
            ("a\\", "trailing backslash"),
            (r"\q", "unknown escape"),
            ("[z-a]", "out of order"),
            ("a{5,2}", "max below min"),
            ("[]", "unterminated"),
            (r"\x4", "hex digits"),
            ("(?P<1bad>a)", "invalid group name"),
            ("(?P<x>a)(?P<x>b)", "duplicate group name"),
            ("(?#comment)", "unsupported group syntax"),
        ],
    )
    def test_syntax_errors(self, pattern: str, fragment: str) -> None:
        with pytest.raises(RegexSyntaxError) as exc:
            compile_pattern(pattern)
        assert fragment in str(exc.value)

    def test_error_points_at_the_right_offset(self) -> None:
        with pytest.raises(RegexSyntaxError) as exc:
            compile_pattern("abc(de")
        assert exc.value.pos == 3
        assert "^" in str(exc.value)

    def test_repeat_limit(self) -> None:
        compile_pattern("a{%d}" % MAX_REPEAT)
        with pytest.raises(RegexSyntaxError) as exc:
            compile_pattern("a{%d}" % (MAX_REPEAT + 1))
        assert "exceeds the limit" in str(exc.value)


class TestRefusedOnPurpose:
    """Constructs turned down for a stated reason, not by oversight."""

    @pytest.mark.parametrize("pattern", [r"(a)\1", r"(a)\2", r"(?P<x>a)\k<x>"])
    def test_backreferences_explain_themselves(self, pattern: str) -> None:
        with pytest.raises(RegexSyntaxError) as exc:
            compile_pattern(pattern)
        message = str(exc.value)
        assert "backreference" in message
        assert "linear time" in message

    @pytest.mark.parametrize("pattern", ["a(?=b)", "a(?!b)", "(?<=a)b", "(?<!a)b"])
    def test_lookaround_is_refused_but_not_conflated(self, pattern: str) -> None:
        with pytest.raises(RegexSyntaxError) as exc:
            compile_pattern(pattern)
        message = str(exc.value)
        assert "lookahead" in message or "lookbehind" in message
        # The distinction matters: lookaround is unimplemented, not impossible.
        assert "not implemented" in message


class TestFlags:
    def test_unknown_flag(self) -> None:
        with pytest.raises(ValueError, match="unknown flags"):
            compile_pattern("a", "z")

    def test_known_flags(self) -> None:
        for f in ("", "i", "s", "m", "ism"):
            compile_pattern("a", f)
