# Deployment checklist

These are steps you run yourself. I can't run them for me-side, and it's
worth being precise about why, because it changes what's worth sending me:

**I can't reach your infrastructure, and shouldn't hold its keys.** Two
separate limits, stated precisely:

- **Supabase is blocked outright** from this build session. Outbound HTTPS
  to `*.supabase.co` is refused by the session's egress policy (403), the
  direct database host is IPv6-only and this sandbox has no IPv6, and the
  IPv4 pooler port times out. No credential changes that.
- **Google Cloud and the model APIs are reachable** — I verified the Gemini
  adapter against Google's live API and got a proper response back. But I
  have no credentials for your Google account, and you shouldn't give me
  any: a key pasted into a chat transcript is a leaked key, and the deploy
  script collects them directly instead.

So: don't send passwords or API keys. Nothing below needs them from you in
chat.

Everything below is therefore written to be done from a browser wherever
possible — no terminal, no local Python — since your primary device is an
iPad.

Nothing here costs money at Stage 0 usage levels if you stick to the
tiers named below — see `BUDGET.md`.

## 1. Database — Supabase (free tier)

**Your project:** `ggnyypoopkmhfgtbqync` — already created, nothing to set
up here. Dashboard: https://supabase.com/dashboard/project/ggnyypoopkmhfgtbqync

### 1a. Apply the schema (do this from any browser, iPad included)

You do **not** need Python, a terminal, or this repo on your device.

1. Open the SQL Editor:
   https://supabase.com/dashboard/project/ggnyypoopkmhfgtbqync/sql/new
2. Open `db/manual_setup.sql` from this repo on GitHub, click the "Copy raw
   file" button, and paste the whole thing into the editor.
3. Press **Run**.

You should see a success message. To confirm it worked, open the Table
Editor — you should now have six tables: `memories`, `tasks`, `audit_log`,
`approvals`, `system_control`, and `schema_migrations`. Click `approvals`
and you should see your five approval defaults already filled in
(research/drafting → `auto`, publishing/spending/credentials →
`ask_every_time`).

That file is safe to run twice — every statement in it is written to skip
work that's already been done, and the whole thing runs as a single
transaction, so a failure partway through leaves the database untouched
rather than half-built.

(The alternative, if you ever do have a terminal handy:
`DATABASE_URL="<your connection string>" python3 db/migrate.py`. Both
paths record the same bookkeeping, so you can freely switch between them
without anything being applied twice.)

### 1b. Get the connection string for the deployed service

Tap **Connect** in the header bar at the top of your project page (not
under Settings — Supabase moved it). Direct link:
https://supabase.com/dashboard/project/ggnyypoopkmhfgtbqync?showConnect=true

In that panel, **copy whichever string has the word `pooler` in its
address.** That's the whole rule. It looks like:

```
postgresql://postgres.ggnyypoopkmhfgtbqync:[YOUR-PASSWORD]@aws-0-<region>.pooler.supabase.com:5432/postgres
```

Replace `[YOUR-PASSWORD]` with your database password (Settings →
Database → Reset database password if you don't have it). That whole
string is your `DATABASE_URL`.

**Why the pooler and not the direct connection** — this is a correction to
what I originally wrote here, and I found it by actually testing against
your project rather than assuming:

- Supabase's direct database host (`db.ggnyypoopkmhfgtbqync.supabase.co`)
  now resolves to an **IPv6-only** address. I confirmed this on your
  project specifically.
- Google Cloud Run's outbound networking is IPv4. So a direct connection
  string would deploy fine and then fail to reach the database at runtime
  — the most annoying category of bug, because everything *looks* correct.
- The pooler hosts are reachable over IPv4, which sidesteps it entirely.
  Supabase also charges extra for an IPv4 add-on on direct connections;
  the pooler is free.

**Session pooler or Transaction pooler — either is fine.** The code
detects which one you gave it and adjusts how it talks to the database
(`core/app/db.py`), so there is no wrong choice between those two. The
only string to avoid is **Direct connection**, which has no `pooler` in
its address and is unreachable from most hosts (see above).

## 2. Auth — Firebase

This is what stops anyone else who finds your JARVIS URL from using it.
All of it is browser work — fine from an iPad.

### 2a. Create the project

1. Go to https://console.firebase.google.com and click **Create a project**.
   Name it whatever you like (`jarvis` is fine).
2. It'll offer Google Analytics — **turn it off**. You don't need it, and
   it's one less thing collecting data.

### 2b. Turn on Google sign-in

1. In the left sidebar: **Build → Authentication → Get started**.
2. Under **Sign-in method**, click **Google**, toggle it **Enable**.
3. Pick a support email (your own), then **Save**.

Google sign-in is the right choice here over email/password: no password
to type on an iPad keyboard, and Google guarantees the email address is
verified, which the next step relies on.

### 2c. Register a web app

1. Click the **gear icon → Project settings**.
2. Scroll to **Your apps**, click the **web icon** (`</>`).
3. Give it a nickname (`jarvis-web`), **don't** tick Firebase Hosting,
   click **Register app**.
4. Firebase shows you a code block. You need exactly four values from it:
   `apiKey`, `authDomain`, `projectId`, `appId`.

These are public identifiers that ship inside any web page using Firebase,
not credentials — they name your project the way a street address names a
house. What actually protects JARVIS is the owner check in 2e.

**Already done for your project.** These values are baked into
`infra/deploy.sh`, so there's nothing to copy:

| | |
|---|---|
| `projectId` | `jarvis-by-claude-a1026` |
| `authDomain` | `jarvis-by-claude-a1026.firebaseapp.com` |
| `apiKey` | `AIzaSyCezIYP0fAG-Yq55Z43W4qdwLXEKzC4g1M` |
| `appId` | `1:701562415519:web:1d377f036cd62c4c66b5aa` |

(`storageBucket` and `messagingSenderId` were in the block you copied but
aren't needed — JARVIS uses Firebase only for sign-in, not storage or
messaging.)

### 2d. Service account key — never needed

Firebase's *Project settings → Service accounts* tab offers a **Generate
new private key** button. **Don't.** JARVIS never uses it.

Checking that a sign-in is genuine only needs Google's **public** keys,
which anyone can read. JARVIS fetches those and verifies the signature
itself (`core/app/auth.py`). No secret key, nothing to store, nothing that
can leak — and it works identically on Render, Google Cloud, or a Mac mini
in your house.

(I originally said to skip this because Google Cloud would supply the
credentials automatically. That was true but narrower: it only held while
JARVIS ran on Google. Verifying against the public keys is better —
it needs no credentials anywhere.)

### 2e. Say who the owner is

Set `OWNER_EMAIL` to the Google address you'll sign in with — for you,
`sagarnandani99@gmail.com`. That's it.

Only that address gets in. Everyone else who signs in gets a clear "this
JARVIS is configured for a single owner and this account is not it."

Two details worth knowing:

- The check only accepts an email Firebase reports as **verified**.
  Without that, someone could register an unverified account claiming your
  address and be let straight in. Google sign-in always reports verified,
  so this is invisible to you — it just closes the hole.
- There's also an `OWNER_UID` setting (Firebase's internal user ID). It's
  more precise, but you can only look it up *after* signing in at least
  once — which would mean deploying, signing in, copying the ID, and
  deploying again. Using your email avoids that entirely. You can set
  `OWNER_UID` later as well if you ever change email address; either one
  matching lets you in.

If neither is set, JARVIS refuses every request with an error saying so,
rather than defaulting to letting anyone in.

## 3. Model provider API key

JARVIS speaks to a language model through a swappable adapter, so which
provider answers is a setting, not a rewrite. Two are built in:

| | Cost | Notes |
|---|---|---|
| **Google Gemini** (default) | Free tier: ₹0, capped ~250 requests/day | Your choice for Stage 0 |
| **Anthropic Claude** | Sonnet 5: $2 / $10 per million tokens in/out | Optional; acts as automatic fallback |

### 3a. Get a Gemini key (free)

1. Go to https://aistudio.google.com/apikey and sign in.
2. Click **Create API key**.
3. **Create it in a NEW project, not `jarvis-by-claude-a1026`.**

That last point matters and is easy to get wrong. **Enabling billing on a
Google Cloud project removes its Gemini free tier** — every call then bills
from the first token. Cloud Run *requires* billing, so the project JARVIS
deploys into will have billing on. A key issued from that same project
would quietly stop being free.

Keeping the Gemini key in its own separate, billing-free project is what
keeps Stage 0 at ₹0.

**Don't paste the key here** — the deploy script asks for it directly and
puts it into Google Secret Manager.

### 3b. Optionally add Claude as a fallback

Not required. If you also set an Anthropic key
([console.anthropic.com](https://console.anthropic.com) → API keys), JARVIS
automatically retries with Claude whenever Gemini fails or is rate-limited,
and the reply tells you which one answered. Without it, a Gemini outage
means JARVIS reports the failure plainly rather than answering.

The deploy script offers this as optional — press Enter to skip.

### 3c. A caution about free tiers

Google revised its free-tier quotas down by 50–80% in December 2025
without notice, and doesn't guarantee them. At your usage that's still
comfortably free, but treat ₹0 as "currently free", not "guaranteed free".
`GET /v1/budget` will keep reporting ₹0 while Gemini is priced at zero —
JARVIS logs a warning at startup saying exactly that, so the zero is never
mistaken for verified proof that nothing is being spent. If you ever
enable billing, set the real prices (see `BUDGET.md`).

## 4. Deploy — Render

**No terminal. No command line. All of this is taps in a browser.**

Render reads `render.yaml` from this repo, so the service arrives already
configured — region, Dockerfile, all the non-secret settings. You supply
two secrets in a web form and press a button.

### 4a. Sign up and connect GitHub

1. Go to **[render.com](https://render.com)** → *Get Started* → **Sign in
   with GitHub**.
2. Authorise Render. When it asks which repositories, you can grant access
   to **only** the JARVIS repo rather than all of them — do that.

Signing in through GitHub is also what gives Render read access to your
private repo, so there's no token to create this time.

### 4b. Create the service from the blueprint

1. In the Render dashboard: **New +** → **Blueprint**.
2. Pick your JARVIS repository.
3. Branch: **`claude/new-session-v0jp79`**.
4. Render reads `render.yaml` and shows a service called **jarvis-core**.
   It will ask you to fill in three values:

| Field | What to paste |
|---|---|
| `DATABASE_URL` | Your Supabase **Session pooler** string (step 1b), with `[YOUR-PASSWORD]` replaced by your real database password |
| `GEMINI_API_KEY` | Your Gemini key (step 3a) |
| `ANTHROPIC_API_KEY` | **Leave blank** unless you want Claude as fallback |

5. **Apply** / **Create**.

Those three are marked `sync: false` in `render.yaml`, which is Render's
way of saying "ask the human, never store this in the repo". That's why
they aren't in the file even though everything else is.

### 4c. Wait for the build

Five to ten minutes on the free tier. The log ends with your service
going **Live** and a URL like:

```
https://jarvis-core-xxxx.onrender.com
```

Visit `<that URL>/health`. You want:

```json
{"status":"ok","emergency_stop":false,"dev_mode":false}
```

`dev_mode` must read **false**. If it ever says true, sign-in is disabled
and anyone could use the service — tell me immediately.

### 4d. Tell Firebase to trust that URL

**Sign-in fails without this**, with an unhelpful "unauthorized domain"
error.

1. Open [Firebase authentication settings](https://console.firebase.google.com/project/jarvis-by-claude-a1026/authentication/settings)
2. **Authorised domains** -> **Add domain**
3. Paste just the host part -- e.g. `jarvis-core-xxxx.onrender.com`, with
   no `https://` and no trailing slash.

### 4e. Allow the sign-in to come back to your address

One more paste, in a different console. Sign-in sends you to Google, and
Google sends you back -- and Google only returns to addresses it has been
told about in advance.

1. Open [Google Cloud credentials](https://console.cloud.google.com/apis/credentials?project=jarvis-by-claude-a1026)
2. Under **OAuth 2.0 Client IDs**, open the one named something like
   *Web client (auto created by Google Service)*
3. Under **Authorised redirect URIs**, click **Add URI** and paste your
   address with `/__/auth/handler` on the end:

```
https://jarvis-core-xxxx.onrender.com/__/auth/handler
```

4. **Save.** It can take a few minutes to take effect.

**Why this step is needed** -- it is genuinely unusual, so it is worth
knowing rather than just following:

Signing in with a pop-up window does not work on iPhone or iPad, because
Safari blocks pop-ups. The obvious alternative, redirecting the whole
page, normally fails too: Firebase's sign-in helper is hosted on its own
domain, separate from the app, and completing the redirect needs those two
domains to share browser storage -- exactly what Safari blocks.

Google's documented answer is to serve that helper from the app's own
domain. JARVIS does this now (`core/app/routes/auth_proxy.py` relays those
few paths), so the browser only ever sees one domain and nothing is
cross-origin. The address Google returns to changes as a result, and this
step is telling Google about it.

### 4f. Turn on the in-page sign-in button

This is the sign-in that works on an iPad. It needs one value from your
Google Cloud console and one setting in Render.

1. Open [Google Cloud credentials](https://console.cloud.google.com/apis/credentials?project=jarvis-by-claude-a1026)
2. Under **OAuth 2.0 Client IDs**, open the one named something like
   *Web client (auto created by Google Service)*
3. Under **Authorised JavaScript origins**, click **Add URI** and paste
   your address with nothing after it:

```
https://jarvis-core-xxxx.onrender.com
```

4. **Save**, then copy the **Client ID** shown on that page. It looks like
   `701562415519-something.apps.googleusercontent.com` and is not a
   secret.
5. In Render: your service -> **Environment** -> add
   `GOOGLE_CLIENT_ID` with that value -> **Save**. The service restarts.

**Why this exists.** Signing in with a pop-up doesn't work on iPad, because
Safari blocks pop-ups. Redirecting the page has its own problems there too.
Google's in-page button avoids both: it neither navigates away nor opens a
window, so no Safari restriction applies.

The server accepts a sign-in from either this button or the older Firebase
route, so a problem with one is never a lock-out. Both are verified the
same way -- against Google's published keys, checking the signature, that
the token was minted for *this* app specifically, and that it hasn't
expired.

### 4g. Make the sign-in stick

Without this, signing in works — and then the page forgets it the moment
it reloads, which includes every time Render redeploys. You tap the button,
it says you're signed in, and a minute later JARVIS says you aren't.

`render.yaml` handles this automatically: it asks Render to generate a
`SESSION_SECRET` and keep it. **If your service was created from the
blueprint after this change, there is nothing to do here.**

If your service already existed, Render won't add it on its own. Check:

1. Render -> your service -> **Environment**
2. Look for `SESSION_SECRET` in the list.

If it isn't there, add it:

3. **Add Environment Variable**, key `SESSION_SECRET`
4. For the value, tap **Generate** if Render offers it. Otherwise type a
   long random string — 30+ characters, anything nobody could guess. It
   doesn't have to mean anything and you never need to remember it.
5. **Save**. The service restarts.

**What it is.** JARVIS gives your browser a small signed note saying "this
is the owner". The note is readable but can't be altered, because it's
signed with this secret. Every later request just hands the note back —
which is why the sign-in survives reloads.

**Why it has to be kept, not regenerated.** Change this value and every
note signed with the old one stops being recognised, so you're signed out.
Left unset entirely, the server invents a new one on every start, which
means every redeploy signs you out — the exact problem this fixes. The
server logs a warning when that's happening, so it's never silent.

**Treat it as a real secret.** Anyone who knows it can sign a note claiming
to be you. It belongs in Render's environment settings only — never in the
repo, never in a chat message.

### What the free tier costs you

Not money — responsiveness. A free Render service **sleeps after 15
minutes of no use**, and the next request takes about a minute to wake it.
So JARVIS will feel instant while you're using it and slow on the first
message after a break. Fine for proving Stage 0 works; worth revisiting
before JARVIS becomes something you rely on during a working day.

### Deploying changes later

Push to the branch and Render rebuilds automatically (`autoDeploy: true`).
Nothing to run.

---

## 4-alt. Deploy — Google Cloud Run (the other option)

`infra/deploy.sh` deploys the same container to Google Cloud Run instead.
It's kept because Cloud Run doesn't sleep, so it stays snappy, and it's a
better home once JARVIS is doing real work.

Two reasons it isn't the default any more:

- It needs a command line (Google Cloud Shell in a browser tab works, but
  it's fiddly on an iPad).
- Cloud Run, Cloud Build and Artifact Registry all require a **billing
  account attached to the project** — a card on file — even though usage
  at this scale stays inside the free allowance. Render's free tier does
  not require that up front.

If you'd rather go this way, everything for it is in `infra/deploy.sh`;
run `bash infra/deploy.sh` from Cloud Shell after cloning the repo.

## Schema changes after the first setup

**Nothing to do.** JARVIS applies its own migrations when it starts: it
compares the migration files it ships with against what the database has
already had, and applies whatever is missing, once.

You only need the SQL Editor for the very first setup, before JARVIS
exists to do it for itself.

If you ever want to check, `docker compose logs jarvis` or Render's log
tab shows a line like `Applied 1 migration(s): 002_agent_foundation.sql`.
A migration that fails is rolled back whole, logged loudly, and does not
stop JARVIS running on the schema it already had — conversation, memory
and voice keep working while you sort it out.

Set `AUTO_MIGRATE=false` if you would rather apply them yourself.

## 5. Verify the full loop

1. Open the deployed URL in a browser (iPad included).
2. Sign in with Google.
3. **Reload the page.** It should still say you're signed in. If it goes
   back to showing the sign-in button, `SESSION_SECRET` isn't set — see
   step 4g.
4. Send a message, confirm you get a reply.
5. Click "Load recent memories" and "Load recent audit log" — confirm the
   exchange shows up with `origin: stated` for your message and
   `origin: retrieved` for JARVIS's reply.
6. Click "Load budget status" — confirm it shows a small non-zero spend
   after step 4.

That's the Stage 0 Definition of Done's "one real request/response
round-trip works end-to-end and is visible in memories and audit_log with
correct provenance" — confirmed from your own device, which is the actual
sign-off condition, not just this doc.
