# Budget tracking — what it covers and what it doesn't

## What counts toward the number `GET /v1/budget` shows you

Every call to the language model. Specifically: each time `/v1/message`
calls Claude, the API tells us exactly how many input and output tokens
that call used. We multiply those by a price-per-token you configure
(`PRICE_INPUT_USD_PER_1M`, `PRICE_OUTPUT_USD_PER_1M` — defaults are
placeholder figures, **verify them against
[anthropic.com/pricing](https://www.anthropic.com/pricing) and update the
env vars if they've drifted**), convert to INR at a configurable rate
(`USD_TO_INR_RATE`, also an estimate — real exchange rates move), and add
it to that calendar month's running total in `audit_log`.

This is a real calculation from real usage, every time — not a guess and
not manually typed in. But it's still an *estimate*: Anthropic doesn't
expose a real-time billing API, so nothing here is pulled from an actual
invoice. Check your Anthropic Console billing page occasionally to confirm
the estimate hasn't drifted from what you're actually being charged
(pricing changes or a wrong exchange rate are the two ways it could).

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
