import inspect
import logging
from typing import Any, Callable, Iterable, TypeVar, get_args, get_type_hints

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
    if inspect.isasyncgenfunction(func):
        async def asyncgen_wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
            if getattr(self, "_is_running", False):
                raise SafeExecutionError(
                    f"Skipped <{func.__name__}> call because {self} is running."
                )
            async for item in func(self, *args, **kwargs):
                yield item

        return asyncgen_wrapper  # type: ignore[return-value]

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


def _injectable_type(hint: Any) -> type | None:
    """Return ContextQueue or ContextPool if hint is or wraps one; else None."""
    from pygents.context import ContextPool, ContextQueue
    for candidate in (ContextQueue, ContextPool):
        if hint is candidate:
            return candidate
    for arg in get_args(hint):
        for candidate in (ContextQueue, ContextPool):
            if arg is candidate:
                return candidate
    return None


def _inject_context_deps(fn: Callable[..., Any], merged: dict[str, Any]) -> dict[str, Any]:
    """Inject ContextQueue/ContextPool for typed params not already in merged."""
    from pygents.context import ContextPool, ContextQueue
    from pygents.context import _current_context_pool, _current_context_queue
    try:
        hints = get_type_hints(fn)
    except Exception:
        return merged
    injected: dict[str, Any] = {}
    for name, hint in hints.items():
        if name == "return" or name in merged:
            continue
        t = _injectable_type(hint)
        if t is ContextQueue:
            val = _current_context_queue.get()
            if val is not None:
                injected[name] = val
        elif t is ContextPool:
            val = _current_context_pool.get()
            if val is not None:
                injected[name] = val
    return {**injected, **merged}  # merged (explicit) always wins


def rebuild_hooks_from_serialization(hooks_data: dict[str, list[str]]) -> list[Any]:
    """Rebuild hook list from serialized data by looking up names in HookRegistry."""
    from pygents.registry import HookRegistry

    seen: set[str] = set()
    result: list[Any] = []
    for _type_str, hook_names in hooks_data.items():
        for hname in hook_names:
            if hname not in seen:
                seen.add(hname)
                result.append(HookRegistry.get(hname))
    return result


def serialize_hooks_by_type(hooks: Iterable[Any]) -> dict[str, list[str]]:
    """Serialize hooks by type."""
    hooks_dict: dict[str, list[str]] = {}
    for h in hooks:
        t = getattr(h, "type", None)
        if t is None:
            continue
        types_to_add = t if isinstance(t, (tuple, frozenset)) else (t,)
        hook_name = getattr(h, "__name__", "hook")
        for single_type in types_to_add:
            key = (
                single_type.value if hasattr(single_type, "value") else str(single_type)
            )
            hooks_dict.setdefault(key, []).append(hook_name)
    return hooks_dict
