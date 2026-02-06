from enum import Enum


class ToolType(str, Enum):
    REASONING = "reasoning"
    ACTION = "action"
    MEMORY_READ = "memory_read"
    MEMORY_WRITE = "memory_write"
    COMPLETION_CHECK = "completion_check"


class StopReason(str, Enum):
    COMPLETED = "completed"
    TIMEOUT = "timeout"
    ERROR = "error"
    CANCELLED = "cancelled"
