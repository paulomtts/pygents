# import asyncio

# from app.enums import ToolType
# from app.tool import Tool
# from app.turn import Turn


# class Agent:
#     """
#     An Agent is an orchestrator. It runs an event loop that processes Turns from a queue. It controls its flow via a set of policies.
#     """

#     name: str
#     description: str

#     def __init__(self, name: str, description: str, tools: list[Tool]):
#         self.name = name
#         self.description = description
#         self.tools = tools

#         self._queue: asyncio.Queue[Turn] = asyncio.Queue()

#     def pop(self) -> Turn:
#         """
#         Pop a Turn from the queue.
#         """
#         return self._queue.get()

#     def put(self, turn: Turn):
#         """
#         Put a Turn on the queue.
#         """
#         if turn.tool not in self.tools:
#             raise ValueError(
#                 f"Agent {self.name!r} does not accept tool {turn.tool.name!r}"
#             )
#         self._queue.put(turn)

#     def run(self):
#         """
#         Run the event loop.
#         """
#         while True:
#             turn = self.pop()
#             result = turn.run()

#             if isinstance(result, Turn):
#                 if turn.tool.type == ToolType.COMPLETION_CHECK and turn.output is True:
#                     break

#                 self._queue.put(result)
#             elif result is not None:
#                 # TODO: if coroutine is yielding, yield the result
#                 # TODO: otherwise return the full result
#                 pass
#             else:
#                 break
#             break
