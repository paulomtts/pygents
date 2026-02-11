import inspect
import logging
from typing import Any, Callable, Iterable, TypeVar

from pygents.errors import SafeExecutionError

R = TypeVar("R")
_function_type = type(lambda: None)


class _NullLock:
    async def __aenter__(self) -> None:
        pass

    async def __aexit__(self, *args: Any) -> None:
        pass


_null_lock = _NullLock()


log = logging.getLogger("pygents")


def safe_execution(func: Callable[..., R]) -> Callable[..., R]:
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> R:
        if not getattr(self, "_is_running", False):
            return func(self, *args, **kwargs)
        raise SafeExecutionError(
            f"Skipped <{func.__name__}> call because {self} is running."
        )

    return wrapper


def validate_fixed_kwargs(
    fn: Callable[..., Any],
    fixed_kwargs: dict[str, Any],
    kind: str = "Tool",
) -> None:
    """Raise TypeError if fixed_kwargs contains keys not in fn's signature and fn has no **kwargs."""
    params = inspect.signature(fn).parameters
    has_var_kwargs = any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
    )
    if not has_var_kwargs:
        valid_keys = set(params.keys())
        invalid = set(fixed_kwargs.keys()) - valid_keys
        if invalid:
            raise TypeError(
                f"{kind} {fn.__name__!r} fixed kwargs {sorted(invalid)} are not in "
                "function signature and function does not accept **kwargs."
            )


def eval_args(args: Iterable[Any]) -> list[Any]:
    return [v() if isinstance(v, _function_type) else v for v in args]


def eval_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    return {k: v() if isinstance(v, _function_type) else v for k, v in kwargs.items()}


def merge_kwargs(
    fixed_kwargs: dict[str, Any],
    call_kwargs: dict[str, Any],
    label: str,
) -> dict[str, Any]:
    evaluated = eval_kwargs(fixed_kwargs)
    for key in call_kwargs:
        if key in evaluated:
            log.warning(
                "Fixed kwarg %r is overridden by call-time argument for %s.",
                key,
                label,
            )
    return {**evaluated, **call_kwargs}


def hooks_by_type_for_serialization(hooks: Iterable[Any]) -> dict[str, list[str]]:
    by_type: dict[str, list[str]] = {}
    for h in hooks:
        t = getattr(h, "hook_type", None)
        if t is not None:
            key = t.value if hasattr(t, "value") else str(t)
            by_type.setdefault(key, []).append(getattr(h, "__name__", "hook"))
    return by_type
