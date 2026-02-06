from enum import Enum
from typing import Any, Awaitable, Callable

Hook = Callable[..., Awaitable[None]]


class TurnHook(str, Enum):
    BEFORE_RUN = "before_run"
    AFTER_RUN = "after_run"
    ON_TIMEOUT = "on_timeout"
    ON_ERROR = "on_error"
    ON_VALUE = "on_value"


class AgentHook(str, Enum):
    BEFORE_TURN = "before_turn"
    AFTER_TURN = "after_turn"
    ON_TURN_VALUE = "on_turn_value"
    ON_TURN_ERROR = "on_turn_error"
    ON_TURN_TIMEOUT = "on_turn_timeout"
    BEFORE_PUT = "before_put"
    AFTER_PUT = "after_put"


class ToolHook(str, Enum):
    BEFORE_INVOKE = "before_invoke"
    AFTER_INVOKE = "after_invoke"


async def run_hooks(hooks: list[Hook], *args: Any, **kwargs: Any) -> None:
    for hook in hooks:
        await hook(*args, **kwargs)
