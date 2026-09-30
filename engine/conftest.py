import sys
import pathlib

# Let tests import shared helpers (engine_fakes) by module name.
sys.path.insert(0, str(pathlib.Path(__file__).parent / "tests"))


def pytest_collection_modifyitems(config, items):
    # Real-model tests only run when selected with -m models.
    if "models" in (config.getoption("-m") or ""):
        return
    import pytest

    skip = pytest.mark.skip(reason="needs ML models; run with -m models")
    for item in items:
        if "models" in item.keywords:
            item.add_marker(skip)
