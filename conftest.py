"""Make the package importable from a fresh clone, without installing it first."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
