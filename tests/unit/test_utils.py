import pytest

from pygents.errors import SafeExecutionError
from pygents.utils import eval_kwargs, safe_execution


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


def test_safe_execution_uses_getattr_so_missing_is_false():
    class NoFlag:
        @safe_execution
        def run_me(self) -> str:
            return "ok"

    obj = NoFlag()
    assert obj.run_me() == "ok"
