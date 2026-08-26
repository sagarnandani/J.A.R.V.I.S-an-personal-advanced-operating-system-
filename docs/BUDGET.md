# Budget tracking — what it covers and what it doesn't

## What counts toward the number `GET /v1/budget` shows you

Every call to a language model, priced by **which provider actually
answered** — not which one was configured. That distinction matters
because JARVIS falls back to the other provider if the primary fails, and
billing a Gemini answer at Claude's rates (or the reverse) would be wrong.

Each call reports its real token counts; those are multiplied by the
per-provider prices below, converted to INR, and added to the month's
running total in `audit_log`.

| Setting | Default | Meaning |
|---|---|---|
| `PRICE_GEMINI_INPUT_USD_PER_1M` | `0.00` | Gemini free tier |
| `PRICE_GEMINI_OUTPUT_USD_PER_1M` | `0.00` | Gemini free tier |
| `PRICE_CLAUDE_INPUT_USD_PER_1M` | `2.00` | Claude Sonnet 5 |
| `PRICE_CLAUDE_OUTPUT_USD_PER_1M` | `10.00` | Claude Sonnet 5 |
| `USD_TO_INR_RATE` | `90` | Estimate, not a live rate |

### The zero-price trap — read this one

Gemini defaults to **₹0 because the plan is Google's free tier.** If
billing is ever enabled on the Google project your Gemini key belongs to,
the free tier stops applying and real charges begin — while these zeros
would keep reporting **₹0 spent forever.**

That's a silent failure in the dangerous direction: the budget guard says
"you're fine" while money is actually going out. Two things guard against
it:

- JARVIS logs a warning at every startup whenever the active provider is
  priced at zero, saying exactly this.
- This paragraph, so the ₹0 on your dashboard is never mistaken for
  verified proof that nothing is being spent.

If you enable billing, set the real rates (Gemini 2.5 Pro was $1.25 in /
$10 out per million tokens as of August 2026 — check
[ai.google.dev/pricing](https://ai.google.dev/pricing)).

### It's an estimate, not a bill

These are real calculations from real token counts, but neither provider
exposes live billing through their API, so nothing here comes from an
actual invoice. Check the provider's own billing page occasionally —
price changes or a drifted exchange rate are the two ways this can go
wrong. Also note Gemini 2.5 models charge for internal "thinking" tokens
that never appear in the reply; JARVIS counts those (many tools forget to),
so its figure should if anything be slightly *higher* than a naive one.

## What does NOT count toward this number

- **Cloud Run compute** — expected to stay at ₹0 at Stage 0 usage
  (scale-to-zero, generous free tier). Check the Cloud Run billing page if
  you want to confirm.
- **Supabase (Postgres)** — ₹0 on the free tier at this scale. Check your
  Supabase project's usage page.
- **Firebase Auth / Cloud Messaging** — ₹0, free tier is far beyond a
  single user's usage.

Integrating all three of those billing APIs into one live dashboard is
real work for very little payoff at one-user scale — flagged here as a
deliberate Stage 0 trade-off (per the build brief's instruction to flag
rather than silently skip) rather than built now. If usage ever grows
enough that this stops being obviously true, that integration moves up
the priority list.

## Alerting

`GET /v1/budget` reports a `status` field:

| status | meaning |
|---|---|
| `ok` | under 50% of `MONTHLY_BUDGET_INR` |
| `warn_50` | over 50% |
| `warn_80` | over 80% |
| `exceeded` | over 100% |

Crossing a threshold is logged server-side (visible in Cloud Run logs).
**Email/push notification on threshold crossing is not built yet** —
today you have to check `/v1/budget` (or the test console's "Load budget
status" button) yourself. This is flagged rather than silently left out;
wiring it to Firebase Cloud Messaging (already provisioned for push) is a
small, well-scoped Stage 1 addition once there's a real client to receive
the push.

## Changing the ceiling

`MONTHLY_BUDGET_INR` is a plain environment variable — change it and
redeploy, no code or schema change needed. The Stage 0 default is ₹3,500,
the midpoint of the brief's stated ₹3,000–4,000 range (see
`docs/ARCHITECTURE.md`, section P).
