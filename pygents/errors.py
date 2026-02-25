class SafeExecutionError(Exception):
    """
    Raised when a method is called or a property is set while the turn is running,
    or when mutating Agent properties while the agent is running or paused.
    """


class WrongRunMethodError(Exception):
    """
    Raised when returning() is used for a yielding tool (use yielding()), or
    yielding() is used for a single-value tool (use returning()).
    """


class TurnTimeoutError(TimeoutError):
    """Raised when a Turn exceeds its timeout while running."""


class UnregisteredToolError(KeyError):
    """Raised when a tool name is not found in ToolRegistry."""


class UnregisteredAgentError(KeyError):
    """Raised when an agent name is not found in AgentRegistry."""


class UnregisteredHookError(KeyError):
    """Raised when a hook name is not found in HookRegistry."""
