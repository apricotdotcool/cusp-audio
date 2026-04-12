import pytest

from cusp.config import CuspConfig


@pytest.fixture
def make_config():
    """Factory fixture that returns a CuspConfig with optional overrides."""

    def _make(**overrides):
        return CuspConfig(**overrides)

    return _make


@pytest.fixture
def default_config():
    return CuspConfig()
