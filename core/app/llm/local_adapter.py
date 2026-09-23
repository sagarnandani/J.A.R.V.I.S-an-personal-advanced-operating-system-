"""A model running on the owner's own hardware.

Sections 13 and 14: the cheap, high-volume work does not need a frontier
model and should not be paid for at frontier rates. On a ~3,500 rupee
ceiling the difference between "free" and "a fraction of a paisa each"
is the difference between JARVIS noticing things all day and JARVIS
rationing itself.

**It speaks OpenAI's API, which is not an endorsement of OpenAI.** It is
the shape everything local already speaks: Ollama, llama.cpp's server,
LM Studio, vLLM, text-generation-webui. So JARVIS does not care what is
serving the model or what hardware it is on -- a laptop, a GPU, a
Raspberry Pi. Point `LOCAL_LLM_URL` at it and it works, and swapping
Ollama for something else is a URL change rather than a new adapter.

**The address is deliberately NOT checked against the network rules.**
`app/agents/tools/fetch.py` refuses every address on the local network,
for good reason: those addresses come from web pages and documents. This
one comes from the owner's own configuration and is SUPPOSED to be
`localhost:11434` or a box in the next room. Running the same check here
would refuse the only addresses this feature exists to reach. The two
rules look contradictory and are not: the question is never "is this
address private", it is "who chose it".

**It is never the fallback for something important.** A 7B model on a
home server is a real model with real limits, and the honest place for
it is the cheap tier -- short, high-volume, low-stakes work. The router
puts it there and nowhere else, and if it is not running, the task goes
to a paid model rather than quietly returning something worse.
"""
import logging

import httpx

from app.llm.base import LLMProvider, LLMResult, Turn, system_prompt_with

logger = logging.getLogger("jarvis.llm.local")

# Long, because a model on a home CPU is slow and a timeout that fires
# mid-answer is worse than waiting. Still bounded: a hung local server
# must not hold a request open for ever.
TIMEOUT = 120.0


class LocalUnavailable(RuntimeError):
    """The local model is not answering. Says what to check."""


class LocalAdapter(LLMProvider):
    """Anything that speaks OpenAI's chat completions API, locally."""

    def __init__(self, base_url: str, model: str, api_key: str = "") -> None:
        # Ollama and llama.cpp accept any key, or none. Sent only if the
        # owner set one, for the case where they put it behind something.
        self._base = (base_url or "").rstrip("/")
        if not self._base.endswith("/v1"):
            self._base += "/v1"
        self._model = model
        self._key = api_key

    async def complete(
        self,
        message: str,
        history: list[Turn] | None = None,
        memory_context: str | None = None,
    ) -> LLMResult:
        messages = [{"role": "system", "content": system_prompt_with(memory_context)}]
        messages += [{"role": t.role, "content": t.text} for t in history or []]
        messages.append({"role": "user", "content": message})

        headers = {"Content-Type": "application/json"}
        if self._key:
            headers["Authorization"] = f"Bearer {self._key}"

        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                response = await client.post(
                    f"{self._base}/chat/completions",
                    headers=headers,
                    json={"model": self._model, "messages": messages,
                          "max_tokens": 1024, "stream": False},
                )
        except httpx.TimeoutException as exc:
            raise LocalUnavailable(
                f"The local model at {self._base} did not answer within "
                f"{TIMEOUT:.0f} seconds. On a CPU that can simply mean the "
                f"model is too big for the machine."
            ) from exc
        except httpx.HTTPError as exc:
            raise LocalUnavailable(
                f"Nothing is answering at {self._base}. Check the server is "
                f"running (for Ollama: `ollama serve`), and that "
                f"LOCAL_LLM_URL points at it."
            ) from exc

        if response.status_code == 404:
            raise LocalUnavailable(
                f"The local server is running but has no model called "
                f"'{self._model}'. Pull it first (for Ollama: "
                f"`ollama pull {self._model}`), or set LOCAL_LLM_MODEL to "
                f"one it already has."
            )
        if response.status_code >= 400:
            raise LocalUnavailable(
                f"The local model at {self._base} answered "
                f"{response.status_code}: {response.text[:200]}"
            )

        try:
            body = response.json()
            choice = body["choices"][0]
            text = choice["message"]["content"] or ""
        except (ValueError, KeyError, IndexError) as exc:
            raise LocalUnavailable(
                f"The server at {self._base} answered, but not in the shape "
                f"an OpenAI-compatible API answers in. Is it actually a "
                f"model server?"
            ) from exc

        usage = body.get("usage") or {}
        return LLMResult(
            text=text,
            # Reported as zero when absent rather than guessed -- a local
            # model costs nothing either way, but a number invented here
            # would end up in the same tables as the real ones.
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            model=self._model,
            provider="local",
        )


async def reachable(base_url: str, model: str = "") -> tuple[bool, str]:
    """Is there actually a model server there. Never raises.

    Used to decide whether the cheap tier can go local, so it has to be
    fast and it has to be honest: a wrong "yes" sends every cheap task to
    something that is not there.
    """
    base = (base_url or "").rstrip("/")
    if not base:
        return False, "No local model is configured."
    if not base.endswith("/v1"):
        base += "/v1"

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{base}/models")
    except Exception as exc:  # noqa: BLE001 - any failure is "no"
        return False, f"Nothing answering at {base}: {type(exc).__name__}."

    if response.status_code >= 400:
        return False, f"{base} answered {response.status_code}."

    try:
        names = [m.get("id", "") for m in (response.json().get("data") or [])]
    except ValueError:
        return False, f"{base} answered, but not with a model list."

    if model and model not in names:
        return False, (
            f"The server is running but does not have '{model}'. "
            f"It has: {', '.join(names[:5]) or 'nothing'}."
        )
    return True, (
        f"Running at {base}"
        + (f" with {model}." if model else
           f", serving {', '.join(names[:3]) or 'nothing yet'}.")
    )
