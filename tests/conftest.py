"""
Pytest configuration for the test suite.

Automatically clears HookRegistry._global_hooks before each test to prevent
global hook leakage between tests. Tests that also need AgentRegistry or
ToolRegistry cleanup must call those manually.
"""

import asyncio

import pytest

from pygents.registry import HookRegistry


@pytest.fixture
def collect_async():
    """Run an async iterator to completion and return the list of items."""

    def _run(agen):
        async def run():
            return [x async for x in agen]
        return asyncio.run(run())

    return _run


@pytest.fixture(autouse=True)
def clear_global_hooks():
    """Reset global hooks before every test for clean isolation."""
    HookRegistry._global_hooks = []
    yield
    HookRegistry._global_hooks = []
