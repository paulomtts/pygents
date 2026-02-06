class SafeExecutionError(Exception):
    """
    Raised when a method is called or a property is set while the turn is running.
    """


class WrongRunMethodError(Exception):
    """
    Raised when run() is used for a yielding tool (use run_yielding()), or run_yielding() is used for a single-value tool (use run()).
    """


class TurnTimeoutError(TimeoutError):
    """Raised when a Turn exceeds its timeout while running."""


class UnregisteredToolError(KeyError):
    """Raised when a tool name is not found in ToolRegistry."""


class UnregisteredAgentError(KeyError):
    """Raised when an agent name is not found in AgentRegistry."""
