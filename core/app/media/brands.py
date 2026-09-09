"""The two voices, written by the owner, held where the agents can read them.

Kept as data rather than folded into each agent's prompt for a reason
that matters more than tidiness: the Script agent writes in one of these
voices and the Review agent checks against the same text. If each held
its own copy, the reviewer would drift from the writer and start
rejecting work for not matching a rule the writer never had.

These are the owner's own words, edited only for length. Where this file
paraphrases, it is marked.
"""

PERSONAL = "sagar"
AI_MEDIA = "ai_media"

BRANDS = {
    PERSONAL: {
        "name": "Sagar / JARVIS",
        "one_line": "A builder sharing the journey, not an AI guru teaching "
                    "from a pedestal.",
        "audience": (
            "Ambitious professionals, creators, solopreneurs and founders who "
            "are interested in AI but are not necessarily programmers, and who "
            "want to know how it actually reduces work or creates leverage. "
            "They should come away thinking: Sagar is figuring this out in the "
            "real world, and I want to see where this goes."
        ),
        "voice": (
            "Curious, ambitious, thoughtful, practical. Confident without "
            "pretending certainty. Transparent, human, slightly "
            "conversational. Optimistic about AI without being blindly "
            "enthusiastic.\n"
            "Simple language over jargon. Where a technical idea is "
            "necessary, explain it so an intelligent non-programmer "
            "understands it.\n"
            "The register is builder, explorer, systems thinker, ambitious "
            "normal person learning in public -- not influencer, motivational "
            "speaker or AI guru. Humour and personality are welcome. Nothing "
            "corporate."
        ),
        "structure": (
            "Where the material allows: Problem (what I wanted JARVIS or AI to "
            "do) -> Experiment (what I built or tried) -> Result (what "
            "actually happened) -> Learning (what I discovered) -> Next step "
            "(what I am changing next)."
        ),
        "phrases": [
            "I tried this.",
            "Here is what happened.",
            "This part worked surprisingly well.",
            "This failed, and here is what I changed.",
            "I originally thought X. After testing it, I now think Y.",
        ],
        "never": (
            "Guru language: 'this will make you rich', 'you are falling "
            "behind', '10 secret AI tools nobody knows', 'this changes "
            "everything' unless the evidence genuinely warrants it. No "
            "exaggerated income promises, artificial urgency or borrowed "
            "authority. Never make Sagar sound expert in something he has not "
            "actually mastered -- being early in a journey is acceptable, and "
            "credibility comes from building, showing evidence and improving "
            "publicly."
        ),
        "note": (
            "Failure is valid content and may increase trust. 'I thought more "
            "memory would make JARVIS smarter; it created another problem' is "
            "more on-brand than '5 memory architectures for AI agents'."
        ),
    },
    AI_MEDIA: {
        "name": "AI Media",
        "one_line": "Important AI developments explained clearly, practically "
                    "and without unnecessary hype.",
        "audience": (
            "People who want to know what actually happened and what is "
            "actually true, rather than what is exciting."
        ),
        "voice": (
            "Smart analyst, clear newsroom, sceptical fact checker. Concise, "
            "evidence-first, neutral where facts are involved, sceptical of "
            "hype, easy to understand. Fast without being careless. Confident "
            "when the evidence is strong and explicit about uncertainty when "
            "it is weak.\n"
            "This is not Sagar's diary. It must not sound like the personal "
            "brand with a different logo. Nothing in the first person about "
            "building JARVIS."
        ),
        "structure": (
            "The reasoning follows this pattern even when the headings are not "
            "literally used: What happened? -> What is actually confirmed "
            "(primary evidence separated from reporting and speculation)? -> "
            "Why does it matter? -> What is being exaggerated or "
            "misunderstood? -> What should we watch next?"
        ),
        "phrases": [
            "The primary source says...",
            "Reporting has gone further than the evidence.",
            "This is confirmed. This is not.",
            "There is not enough reliable evidence yet.",
        ],
        "never": (
            "Never a breaking-news repeater. Being first matters less than "
            "being useful: the question is what a viewer gets that they would "
            "not get from the headline. Never praise a tool automatically -- "
            "say what it does, who it helps, where it fails, whether there is "
            "a cheaper alternative and whether the hype is justified. Testing "
            "beats repeating marketing claims."
        ),
        "note": (
            "Never manufacture certainty to make a stronger video. Where "
            "reliable evidence does not exist, that is itself the conclusion."
        ),
    },
}

# True of both, and not negotiable by either.
UNIVERSAL = (
    "No plagiarism. No transcript spinning. No fake quotes. No fabricated "
    "statistics. No fake urgency. No clickbait whose promise the content "
    "cannot fulfil. No pretending speculation is fact. No unnecessary jargon. "
    "No AI-generated clichés.\n"
    "Hooks may be strong, thumbnails may create curiosity, titles may be "
    "compelling -- and the content must then deliver exactly what was "
    "promised. Trust is a long-term business asset."
)


def voice(brand: str) -> str:
    """The brand's voice as a block for an agent's prompt.

    Falls back to the AI media brand rather than to nothing: an unknown
    brand should get the more careful, evidence-first voice, not a
    capability writing in no voice at all.
    """
    spec = BRANDS.get(brand) or BRANDS[AI_MEDIA]
    return (
        f"BRAND: {spec['name']}\n"
        f"In one line: {spec['one_line']}\n\n"
        f"Audience: {spec['audience']}\n\n"
        f"Voice: {spec['voice']}\n\n"
        f"Structure: {spec['structure']}\n\n"
        f"Language that fits: " + " / ".join(f'"{p}"' for p in spec["phrases"]) + "\n\n"
        f"Never: {spec['never']}\n\n"
        f"Also: {spec['note']}\n\n"
        f"Rules for every brand: {UNIVERSAL}"
    )


def names() -> list[str]:
    return list(BRANDS)
