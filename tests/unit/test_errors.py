"""
Tests for pygents.errors (public exception API).
"""

import pytest

from pygents.errors import (
    UnregisteredToolError,
    WrongRunMethodError,
)


def test_unregistered_tool_error_message():
    with pytest.raises(UnregisteredToolError, match=r"'missing' not found"):
        raise UnregisteredToolError("'missing' not found")


def test_wrong_run_method_error_mention_yielding():
    e = WrongRunMethodError("Tool is async generator; use yielding() instead.")
    assert "yielding()" in str(e)
