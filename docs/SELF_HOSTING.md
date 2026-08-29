# Running JARVIS on your own machine

Everything on your own disk: JARVIS, its database, your memories. No
Supabase, no Render, no account anywhere.

**On the "Docker image file" you asked for.** You do not want one, and I
cannot honestly give you one. A saved image is a ~600MB binary blob that
has to be rebuilt every time a line of code changes, and it would be
stale before you finished downloading it. What you want is the command
below: it builds the image on your PC from this repo, in about two
minutes, and `git pull && docker compose up -d --build` updates it. Same
result, always current, nothing to transfer.

---

## 1. Install Docker Desktop

[docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop/)
— Windows, Mac or Linux. Start it and wait for the whale icon to settle.

## 2. Get the code

```bash
git clone https://github.com/sagarnandani/J.A.R.V.I.S-an-personal-advanced-operating-system-.git jarvis
cd jarvis
```

## 3. Tell Google about localhost

Google refuses sign-ins from any address not registered against your
OAuth client, and your PC is a new address.

1. Open [Google Cloud credentials](https://console.cloud.google.com/apis/credentials)
2. Open your **OAuth 2.0 Client ID** (the web one)
3. Under **Authorised JavaScript origins**, **Add URI**:
   ```
   http://localhost:8080
   ```
4. **Save**, and copy the **Client ID** shown on that page.

*(Skipping this is the single most likely reason the sign-in button will
not appear.)*

## 4. Fill in your settings

```bash
cp .env.example .env
```

Open `.env` in any text editor. Four things are required:

| | |
|---|---|
| `OWNER_EMAIL` | your Google address — the only one that can sign in |
| `GOOGLE_CLIENT_ID` | from step 3 |
| `SESSION_SECRET` | any long random string (the file shows a command to make one) |
| `GEMINI_API_KEY` | free from [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |

`.env` is git-ignored. It never leaves your machine.

## 5. Start it

```bash
docker compose up -d --build
```

First run takes a couple of minutes to build. Then open
**http://localhost:8080**

The database schema applies itself the first time. There is no migration
step to remember.

```bash
docker compose logs -f jarvis    # watch what it's doing
docker compose down              # stop (your data is kept)
docker compose up -d             # start again
```

---

## Bringing your memories across from Supabase

Your existing JARVIS already knows things. Move them over before you
switch off the cloud one.

**Export** — in the Supabase dashboard, Settings → Database → copy your
connection string, then on your PC:

```bash
docker run --rm -it postgres:16-alpine pg_dump \
  --data-only --no-owner \
  -t memories -t audit_log -t system_control \
  "PASTE_YOUR_SUPABASE_CONNECTION_STRING" > jarvis-backup.sql
```

**Import** — with JARVIS running locally:

```bash
docker compose exec -T postgres psql -U jarvis -d jarvis < jarvis-backup.sql
```

`--data-only` matters: the local database already has the tables, and
this brings only the contents. `approvals` is left out deliberately — it
is seeded identically on both sides, and copying it would collide.

Check it worked: open the dashboard and look at **What JARVIS knows**.

## Backups

Nothing backs this up for you any more. That is the real cost of self-hosting.

```bash
docker compose exec -T postgres pg_dump -U jarvis jarvis > jarvis-$(date +%F).sql
```

Run it occasionally, and keep a copy somewhere that is not this machine.
To restore into an empty database, feed the file back in with the import
command above.

---

## Reaching it from your phone or iPad

By default JARVIS listens on `127.0.0.1` — this machine only. To reach it
from other devices in the house, change the port line in
`docker-compose.yml`:

```yaml
    ports:
      - "8080:8080"      # was "127.0.0.1:8080:8080"
```

**Read this before you do.** With plain http, your login cookie crosses
your network in clear text, and anyone on your Wi-Fi could copy it and
become you. Two honest options:

**Tailscale (easiest, and what I would do).** A free private network
between your own devices, with HTTPS included.

```bash
tailscale serve --bg 8080
```

Then set `COOKIE_SECURE=true` in `.env` and restart. Your iPad reaches
JARVIS from anywhere, encrypted, and nothing is exposed to the internet.
Add your Tailscale address to Google's Authorised JavaScript origins
(step 3) as well.

**A reverse proxy with a real certificate** — Caddy or nginx with Let's
Encrypt, if you already run one and have a domain.

Whichever you choose, set `COOKIE_SECURE=true` once HTTPS is working.
JARVIS logs a warning at every startup while it is false, because that
setting is correct on localhost and dangerous anywhere else.

---

## What you gain and what you take on

| | Render (free) | Your PC |
|---|---|---|
| Sleeps after 15 min idle | yes, ~1 min to wake | no |
| Cost | ₹0 | electricity |
| HTTPS and certificates | done for you | yours to set up |
| Reachable when away from home | yes | needs Tailscale or a proxy |
| If power or internet drops | n/a | JARVIS is down |
| Backups | Supabase does it | **yours to run** |
| Where your data lives | someone else's disk | your disk |

The good reason to do this is the last row. It also removes the sleeping
entirely, which is the biggest single thing you noticed being slow.

What it will *not* fix is the rest of the wait: most of a warm reply is
Google answering over the internet, and that is the same whether the
request leaves a data centre or your living room. See `SPEED.md`.

## If something goes wrong

| What you see | What it means |
|---|---|
| No sign-in button | `GOOGLE_CLIENT_ID` missing, or `http://localhost:8080` not added in step 3 |
| Signs in, then immediately signed out | `COOKIE_SECURE` is `true` on plain http — set it to `false` |
| "not the owner" | `OWNER_EMAIL` does not match the Google account you used |
| Placeholder replies about an API key | `GEMINI_API_KEY` missing or wrong |
| Signed out after every restart | `SESSION_SECRET` not set in `.env` |
| Port 8080 already in use | change the left-hand number: `"127.0.0.1:8081:8080"` |

`docker compose logs jarvis` shows what the server thinks is wrong, in
plain language.
