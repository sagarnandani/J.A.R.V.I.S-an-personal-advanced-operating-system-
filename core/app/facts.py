"""Long-term memory: the things worth keeping, not just the recent chat.

Conversation recall (see `memory.recall_turns`) replays the last ~10
exchanges. That is a chat window, not a memory: tell JARVIS your wife's
birthday, talk about anything else for ten minutes, and it is gone.

This module keeps the parts worth keeping. After each exchange JARVIS
reads what was said and writes down any durable fact -- a preference, a
date, a decision, a person -- as its own small memory. Those are looked up
by relevance on later messages, so a fact from months ago comes back when
it is relevant, long after the conversation it came from has scrolled away.

Two design points that matter.

**Provenance.** A fact is JARVIS's own sentence summarising what you said,
so it is stored as `inferred`, not `stated`. That is the honest label: you
said "my wife's birthday is on the 3rd of March"; JARVIS wrote "Owner's
wife's birthday: 3 March". Each fact links back to the verbatim memory it
came from, so the chain from JARVIS's summary to your actual words is
never broken. This is also why facts travel on their own path into the
prompt rather than being smuggled into the conversation replay, which
deliberately carries only what was really said.

**Correcting.** The same pass that extracts facts is shown the facts
JARVIS already holds and asked which are now wrong. Saying "actually it's
blue" retires the old fact instead of leaving two contradictory ones
sitting side by side. Retiring is the same reversible `forget` used
everywhere else -- nothing is destroyed, and the old fact stays visible in
the memory list.
"""
import json
import logging
import re
from uuid import UUID

from app.db import execute, fetch
from app.memory import VALID_CATEGORIES, store_memory

logger = logging.getLogger("jarvis.facts")

# Facts are JARVIS's own words, so they are 'inferred'. See the module
# docstring -- this is the provenance rule doing its job, not a technicality.
FACT_ORIGIN = "inferred"
DEFAULT_FACT_CATEGORY = "semantic"

_EXTRACTION_PROMPT = """\
You maintain the long-term memory of a personal assistant, for one owner.

Read the exchange below and write down anything that will STILL BE TRUE IN
A YEAR.

Do not wait to be asked. The owner will almost never say "remember this".
When they mention their wife's name, their date of birth, their
anniversary, where they work or what they are allergic to, that is
permanent information and you record it -- exactly as you would if they
had asked you to. Judge the INFORMATION, not the phrasing of the
sentence it arrived in.

Always record, whenever they come up:
- People: names and who they are -- wife, children, parents, colleagues.
- Dates: birthdays, anniversaries, deadlines, milestones.
- Places: where they live, work, are from, travel to often.
- Health: conditions, allergies, medication, dietary needs.
- Work and money: job, business, clients, tools, budgets, commitments.
- Preferences and standing instructions: how they like things done.
- Decisions: choices made and the reason, where stated.

Do not record: questions the owner asked, small talk with nothing durable
in it, anything the assistant said about itself, or anything already in
the known facts below.

Write each as a short standalone sentence that still makes sense read
alone in a year. "Owner's wife is called Sneha", not "her name is Sneha".

You are also shown the facts already held. If this exchange makes any of
them WRONG or out of date, list their ids as superseded. Only when they
genuinely conflict -- not merely because they are related.

Known facts:
{known}

Exchange:
OWNER: {user_text}
ASSISTANT: {reply_text}

Separately, note any MONEY the owner states in actual figures -- what
they received or what they spent. Only when they give a real amount.
Never infer one from context, never round "a few thousand" into a number,
and never repeat a figure the assistant itself produced. A ledger that
guesses is worse than no ledger, because it looks like arithmetic.

Reply with JSON only, no other text:
{{"facts": [{{"text": "...", "category": "..."}}], "supersedes": ["id", ...],
  "money": [{{"direction": "in" or "out", "amount_inr": 40000,
             "what": "Bengaluru shoot", "category": "client work"}}]}}

category must be one of: preference, people, decision, project, task,
semantic. An empty facts list is correct when the exchange genuinely
contained nothing durable -- but do not use it to avoid a judgement call.
"""


async def list_facts(limit: int = 200, include_retired: bool = False) -> list:
    if include_retired:
        return await fetch(
            "SELECT * FROM memories WHERE origin = $1 "
            "ORDER BY created_at DESC LIMIT $2",
            FACT_ORIGIN,
            limit,
        )
    return await fetch(
        "SELECT * FROM memories WHERE origin = $1 "
        "AND (expires_at IS NULL OR expires_at > now()) "
        "ORDER BY created_at DESC LIMIT $2",
        FACT_ORIGIN,
        limit,
    )


async def recall_facts(message: str, limit: int, max_chars: int) -> list[str]:
    """The facts worth putting in front of the model for THIS message.

    Relevant ones first, found with Postgres' own full-text search, then
    recent ones to fill the remaining space. Both halves matter: search
    alone misses a fact you mentioned differently ("wife" vs "spouse"),
    and recency alone is what we already had and is why old facts vanish.

    No index and no extension -- at a personal system's scale (hundreds of
    facts, not millions) a plain scan is instant, and needing neither
    keeps this deployable against any Postgres with nothing to run by hand.
    """
    if limit <= 0 or max_chars <= 0:
        return []

    rows = await fetch(
        """
        SELECT content, ts_rank(
                   to_tsvector('english', content),
                   plainto_tsquery('english', $1)
               ) AS rank
        FROM memories
        WHERE origin = $2
          AND (expires_at IS NULL OR expires_at > now())
        ORDER BY rank DESC, created_at DESC
        LIMIT $3
        """,
        message,
        FACT_ORIGIN,
        limit,
    )

    kept: list[str] = []
    remaining = max_chars
    for row in rows:
        text = row["content"]
        if len(text) > remaining:
            break
        remaining -= len(text)
        kept.append(text)
    return kept


def _parse_reply(raw: str) -> tuple[list[dict], list[str], list[dict]]:
    """Read the model's JSON, forgivingly and without ever raising.

    Models wrap JSON in code fences, add a sentence before it, or return
    prose on a bad day. None of that should break the reply the owner is
    waiting on, so anything unparseable means "no facts this time" -- the
    same as a message with nothing worth keeping.
    """
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    else:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            text = text[start : end + 1]

    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        logger.warning("Fact extraction did not return JSON; skipping this one.")
        return [], [], []

    if not isinstance(data, dict):
        return [], [], []

    facts = [f for f in (data.get("facts") or []) if isinstance(f, dict) and f.get("text")]
    supersedes = [s for s in (data.get("supersedes") or []) if isinstance(s, str)]
    money = data.get("money") or []
    return facts, supersedes, (money if isinstance(money, list) else [])


def _normalise(text: str) -> str:
    return " ".join(text.lower().split())


async def _retire(fact_ids: list[UUID]) -> int:
    if not fact_ids:
        return 0
    result = await execute(
        "UPDATE memories SET expires_at = now() WHERE id = ANY($1::uuid[])",
        fact_ids,
    )
    return int(result.rsplit(" ", 1)[-1])


async def _record_money(entries: list[dict], source_memory_id: UUID) -> int:
    """Write down what the owner said they earned or spent.

    Never allowed to break fact learning, which is itself never allowed to
    break the conversation. A dropped row costs one line in a ledger the
    owner can add by saying it again; an exception here would cost the
    memory of the whole exchange.
    """
    from app import money

    written = 0
    try:
        for entry in money.parse(entries):
            if await money.record(**entry, source="stated", said_in=source_memory_id):
                written += 1
        if written:
            logger.info("Recorded %d money movement(s).", written)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not record money: %s", exc)
    return written


async def learn_from_exchange(
    provider, user_text: str, reply_text: str, source_memory_id: UUID, max_facts: int
) -> tuple[int, int]:
    """Write down anything durable from this exchange. Returns (learned, retired).

    Runs after the owner already has their reply, so its cost is never
    time they spend waiting. It does cost an extra model call, which is
    why it can be switched off entirely.
    """
    known = await list_facts(limit=100)
    known_block = (
        "\n".join(f'- [{r["id"]}] {r["content"]}' for r in known) or "(none yet)"
    )

    prompt = _EXTRACTION_PROMPT.format(
        known=known_block, user_text=user_text, reply_text=reply_text
    )
    result = await provider.complete(prompt)
    facts, supersedes, money = _parse_reply(result.text)

    # Money rides on this call rather than costing one of its own. The
    # same pass is already reading the exchange for anything durable, and
    # a figure the owner stated is exactly that.
    await _record_money(money, source_memory_id)

    # Only ids we actually showed it. A model asked for ids will sometimes
    # invent one, and an invented id here would retire a real memory that
    # nothing was ever said against.
    known_ids = {str(r["id"]): r["id"] for r in known}
    retired = await _retire([known_ids[s] for s in supersedes if s in known_ids])

    # Never store a fact twice.
    #
    # The prompt asks the model not to repeat what it already knows, and a
    # good one mostly obliges. Mostly is not good enough: every duplicate
    # eats the fact budget and crowds out something JARVIS would otherwise
    # have remembered, and it degrades quietly -- memory looks full while
    # holding one thing thirteen times. Checked here, where it is certain,
    # rather than asked for in a prompt, where it is a request.
    seen = {_normalise(r["content"]) for r in known}

    learned = 0
    for fact in facts[:max_facts]:
        category = str(fact.get("category", "")).strip().lower()
        if category not in VALID_CATEGORIES:
            category = DEFAULT_FACT_CATEGORY
        text = str(fact["text"]).strip()
        if not text or _normalise(text) in seen:
            continue
        seen.add(_normalise(text))

        await store_memory(
            content=text,
            category=category,
            origin=FACT_ORIGIN,
            # Not 1.0: this is JARVIS's reading of what was said, and the
            # confidence field exists to keep that distinction visible
            # rather than letting a summary harden into a certainty.
            confidence=0.8,
            related_memory_ids=[source_memory_id],
        )
        learned += 1

    return learned, retired
