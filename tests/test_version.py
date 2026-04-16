from importlib.metadata import version

import cusp


def test_version_matches_package_metadata() -> None:
    assert cusp.__version__ == version("cusp-audio")
