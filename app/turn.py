from datetime import datetime
from typing import Any

from app.enums import StopReason
from app.registry import ToolRegistry
from app.tool import Tool


class Turn:
    """
    A Turn is an immutable holder of intent and metadata. It represents a single conceptual unit of work. A Turn may contain constrains (such as timeouts & etc.)

    A Turn should be serializable for storage. It holds intent, describing what should happen - but not how it should happen.
    """

    # TODO: a Turn is very similar to a Grafo Node.
    # TODO: a turn's kwargs should never be by reference, so that it can be serialized and stored - implement validation for this

    tool: Tool | None = None
    kwargs: dict[str, Any] = {}
    output: Any | None = None
    timeout: int = 60
    start_time: datetime | None = None
    end_time: datetime | None = None
    stop_reason: StopReason | None = None

    def __init__(self, tool_name: str, kwargs: dict[str, Any] = {}, timeout: int = 60):
        self.tool = ToolRegistry.get(tool_name)
        self.kwargs = kwargs
        self.timeout = timeout

        ToolRegistry.register(self.tool)

    def run(self):
        """
        Run the Turn.
        """
        # TODO: improve this to actually track timeout & stop reasons
        # TODO: implement safe execution
        # TODO: implement lock so that tools manipulating shared state are safe
        self.start_time = datetime.now()
        self.output = self.tool.execute(**self.kwargs)
        self.end_time = datetime.now()
        self.stop_reason = StopReason.COMPLETED
