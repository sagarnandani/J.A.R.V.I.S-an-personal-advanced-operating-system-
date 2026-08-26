"""Provider-agnostic LLM interface.

This is the seam the architecture doc's section M and the Stage 0 brief's
section 4.2 both call for: "structure this behind an interface so a second
provider can be added later without touching calling code." Nothing
outside this package should ever import a provider SDK directly.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass


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
