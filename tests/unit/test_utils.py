"""
Tests for pygents.utils, driven by the following decision table.

Decision table for pygents/utils.py
-----------------------------------
safe_execution(func):
  SE1  getattr(self, "_is_running", False) is False -> call func(self, *args, **kwargs), return result
  SE2  _is_running is True -> SafeExecutionError with func.__name__ and "running"
  SE3  self has no _is_running -> getattr returns False -> same as SE1

eval_args(args): each item callable (_function_type) -> call and use return value; else pass through.
eval_kwargs(kwargs): same per value, keys unchanged.

merge_kwargs(fixed_kwargs, call_kwargs, label):
  MK1  evaluated = eval_kwargs(fixed_kwargs)
  MK2  key in call_kwargs also in evaluated -> log.warning
  MK3  return {**evaluated, **call_kwargs} (call overrides)

hooks_by_type_for_serialization(hooks):
  HT1  hook has no hook_type or None -> skipped
  HT2  hook_type has .value (enum) -> key = hook_type.value
  HT3  hook_type no .value -> key = str(hook_type)
  HT4  name = getattr(h, "__name__", "hook"); by_type[key].append(name)
"""

import asyncio
import logging

import pytest

from pygents.errors import SafeExecutionError
from pygents.hooks import TurnHook
from pygents.utils import (
    eval_args,
    eval_kwargs,
    merge_kwargs,
    safe_execution,
    serialize_hooks_by_type,
)


def test_eval_kwargs_leaves_non_callables_unchanged():
    result = eval_kwargs({"a": 1, "b": "x", "c": None})
    assert result == {"a": 1, "b": "x", "c": None}


def test_eval_kwargs_calls_lambdas_and_uses_return_value():
    result = eval_kwargs({"x": lambda: 42, "y": lambda: "ok"})
    assert result == {"x": 42, "y": "ok"}


def test_eval_kwargs_mixes_callables_and_static_values():
    result = eval_kwargs({"a": 1, "b": lambda: 2, "c": "three"})
    assert result == {"a": 1, "b": 2, "c": "three"}


def test_eval_kwargs_empty_dict():
    assert eval_kwargs({}) == {}


def test_eval_kwargs_calls_function_at_eval_time():
    counter = [0]

    def make():
        counter[0] += 1
        return counter[0]

    result = eval_kwargs({"n": make})
    assert result == {"n": 1}
    result2 = eval_kwargs({"n": make})
    assert result2 == {"n": 2}


class _RunningObject:
    _is_running: bool = False

    @safe_execution
    def run_me(self, x: int) -> int:
        return x + 1

    @safe_execution
    def run_me_kwargs(self, *, y: int) -> int:
        return y * 2


def test_safe_execution_allows_call_when_not_running():
    obj = _RunningObject()
    assert obj.run_me(3) == 4
    assert obj.run_me_kwargs(y=5) == 10


def test_safe_execution_raises_when_running():
    obj = _RunningObject()
    obj._is_running = True
    with pytest.raises(SafeExecutionError) as exc_info:
        obj.run_me(1)
    assert "run_me" in str(exc_info.value)
    assert "running" in str(exc_info.value).lower()


def test_safe_execution_asyncgen_raises_when_running():
    @safe_execution
    async def gen_fn(self):
        yield 1

    class Obj:
        _is_running = True

    obj = Obj()

    async def _():
        async for _ in gen_fn(obj):
            pass

    with pytest.raises(SafeExecutionError):
        asyncio.run(_())


def test_safe_execution_uses_getattr_so_missing_is_false():
    class NoFlag:
        @safe_execution
        def run_me(self) -> str:
            return "ok"

    obj = NoFlag()
    assert obj.run_me() == "ok"


# --- eval_args -----------------------------------------------------------------------


def test_eval_args_leaves_non_callables_unchanged():
    assert eval_args([1, "x", None]) == [1, "x", None]


def test_eval_args_calls_callables_and_uses_return_value():
    assert eval_args([lambda: 10, lambda: "y"]) == [10, "y"]


def test_eval_args_mixes_callables_and_static_values():
    assert eval_args([1, lambda: 2, "three"]) == [1, 2, "three"]


def test_eval_args_empty_iterable():
    assert eval_args([]) == []


def test_eval_args_calls_at_eval_time():
    counter = [0]

    def make():
        counter[0] += 1
        return counter[0]

    assert eval_args([make]) == [1]
    assert eval_args([make]) == [2]


# --- merge_kwargs --------------------------------------------------------------------


def test_merge_kwargs_merges_fixed_and_call_call_overrides():
    result = merge_kwargs({"a": 1, "b": 2}, {"b": 20, "c": 3}, "label")
    assert result == {"a": 1, "b": 20, "c": 3}


def test_merge_kwargs_override_logs_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="pygents"):
        merge_kwargs({"k": 1}, {"k": 2}, "my_label")
    assert "Fixed kwarg 'k' is overridden" in caplog.text
    assert "my_label" in caplog.text


# --- hooks_by_type_for_serialization --------------------------------------------------


def test_hooks_by_type_for_serialization_empty():
    assert serialize_hooks_by_type([]) == {}


def test_hooks_by_type_for_serialization_uses_enum_value_and_name():
    hook1 = type("H", (), {"type": TurnHook.BEFORE_RUN, "__name__": "my_hook"})()
    result = serialize_hooks_by_type([hook1])
    assert result == {"before_run": ["my_hook"]}


def test_hooks_by_type_for_serialization_skips_hook_without_type():
    hook_no_type = type("H", (), {"__name__": "anonymous"})()
    assert serialize_hooks_by_type([hook_no_type]) == {}


def test_hooks_by_type_for_serialization_name_fallback():
    class E:
        value = "ev"

    hook_no_name = type("H", (), {"type": E()})()
    result = serialize_hooks_by_type([hook_no_name])
    assert result == {"ev": ["hook"]}
