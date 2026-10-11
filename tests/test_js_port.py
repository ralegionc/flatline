"""Hold the JavaScript port to the Python engine.

The visualiser runs its own implementation of the engine so it can step the
machine without a server.  A second implementation is a second chance to be
wrong, and a visualiser that draws something the tested engine would not do is
worse than no visualiser at all.

So the corpus in ``web/conformance.js`` is not a JavaScript test fixture that
happens to look right -- it is generated from the Python engine, and this test
runs it under Node and compares programs, spans, *step counts*, peak thread
counts and parse-error offsets.  Step counts are the tight one: two engines can
agree on every answer while doing different amounts of work, and the work is
the thing this project is about.

Skipped, not failed, when Node is unavailable: the Python engine stands on its
own and should not be unbuildable on a machine without a JavaScript runtime.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CHECKER = ROOT / "tools" / "check_port.js"
CORPUS = ROOT / "web" / "conformance.js"
GENERATOR = ROOT / "tools" / "gen_conformance.py"

node = shutil.which("node")
requires_node = pytest.mark.skipif(node is None, reason="node is not installed")


@requires_node
def test_javascript_port_matches_the_python_engine() -> None:
    proc = subprocess.run(
        [node, str(CHECKER)], capture_output=True, text=True, cwd=str(ROOT), timeout=120
    )
    assert proc.stdout, "checker produced no output; stderr was:\n" + proc.stderr

    report = json.loads(proc.stdout)
    assert report["checked"] > 50, "corpus looks truncated: only {} cases".format(report["checked"])
    assert report["failures"] == [], "the JavaScript port has drifted:\n  " + "\n  ".join(
        report["failures"]
    )


def test_corpus_is_not_stale() -> None:
    """Regenerating the corpus must be a no-op.

    Without this, a change to the Python engine would leave the recorded
    expectations describing the old behaviour, and the port test above would
    keep passing while checking the wrong thing.
    """
    before = CORPUS.read_text(encoding="utf-8")
    subprocess.run(
        ["python", str(GENERATOR)], capture_output=True, text=True, cwd=str(ROOT), check=True
    )
    after = CORPUS.read_text(encoding="utf-8")
    assert before == after, (
        "web/conformance.js is out of date with the Python engine; "
        "run `python tools/gen_conformance.py` and commit the result"
    )
