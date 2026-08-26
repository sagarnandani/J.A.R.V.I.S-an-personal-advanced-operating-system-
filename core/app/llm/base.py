"""Provider-agnostic LLM interface.

This is the seam the architecture doc's section M and the Stage 0 brief's
section 4.2 both call for: "structure this behind an interface so a second
provider can be added later without touching calling code." Nothing
outside this package should ever import a provider SDK directly.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass

# One definition, shared by every provider -- JARVIS should behave the same
# whichever model is answering. If this differed per adapter, switching
# providers would quietly change JARVIS's personality along with it.
JARVIS_SYSTEM_PROMPT = (
    "You are JARVIS, a personal AI operating system. This is Stage 0: a "
    "plain request/response loop with no tools, no agents, and no memory "
    "recall yet. Respond helpfully and briefly."
)


@dataclass
class LLMResult:
    text: str
    input_tokens: int
    output_tokens: int
    model: str
    provider: str


class LLMProvider(ABC):
    @abstractmethod
    async def complete(self, message: str) -> LLMResult:
        """Send a single message, return the reply and token usage."""
