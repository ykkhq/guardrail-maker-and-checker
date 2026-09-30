import pathlib
import sys

# Let tests import shared helpers (studio_fakes) by module name.
sys.path.insert(0, str(pathlib.Path(__file__).parent / "tests"))
