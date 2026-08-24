# Coda API keys, the Speech API, and the voice agent

How to issue a programmatic key, what it opens today, and how the voice agent
is reached over LiveKit.

---

## 1. Status: read this first

Not everything below is live. The key system and the Speech API are deployed and
verified; the realtime agent gateway is designed but **not built yet**.

| Capability | Scope needed | Status |
| --- | --- | --- |
| Issue / revoke keys from the admin dashboard | — | **Live** |
| Per-key usage in minutes | — | **Live** (reads 0 until agent traffic exists) |
| Speech synthesis over HTTP | `tts` | **Live** |
| Voice agent from the Coda website | — | **Live** (session login, no key) |
| Voice agent with an API key | `agent` | **Not yet — gateway not built** |

Issuing an `agent`-scoped key today is legitimate and forward-compatible: the
key, its concurrency ceiling, and its usage accounting all exist. But **nothing
currently accepts that scope**, so such a key opens no door until the realtime
gateway ships. Section 6 describes what that gateway will expose; section 5
describes the LiveKit path the agent already runs on.

---

## 2. Issuing a key

Keys are issued by hand from the admin dashboard and delivered to the recipient
out of band. There is no self-serve signup yet.

**Where:** <https://www.coda.eracoda.ai/admin/keys> → **Issue key**

Exactly one hostname serves `/admin`. `www.coda.eracoda.com` and
`coda.eracoda.sa` redirect there with a `308`, because the dashboard rejects any
mutation whose `Origin` is not exactly `https://www.coda.eracoda.ai`.

**Fields**

| Field | Notes |
| --- | --- |
| Owner email | The account the key bills against. If the address has no Coda account, one is created automatically — see below. |
| Label | Free text, ≤ 60 chars. Shown in the dashboard and in the audit log. |
| Scopes | `agent`, `tts`, `stt`, `llm`. At least one. |
| Max concurrent sessions | 1–100, default 1. Ceiling on simultaneous live agent sessions. |
| Expires | Optional. Must be in the future. |

**What happens to a brand-new email address.** A confirmed, passwordless
Supabase Auth account is created for it. The recipient never logs in, but the
account gives the key a real owner, so plan, quota, usage and revocation all
work from day one — and when self-serve arrives, that person does a password
reset and finds their key and usage history already there. The account starts on
the **free** plan (300 agent seconds per period). Raise it under
**Users → limits**, which sets a per-user override on top of the plan.

**Copy the key immediately.** It is displayed exactly once. Only
`sha256(pepper ‖ key)` is stored, so neither the dashboard nor a database dump
can reproduce it. Lost key → revoke and issue another.

Requires the `owner` or `operator` role. A `viewer` can list keys but not create
or revoke them. Every creation and revocation is written to
`private.admin_audit_log`, deliberately without the hash.

---

## 3. Key anatomy

```
coda_sk_live_K3n8vQ2rT9wXmB4pL7dY6hF1sA0zJ5cE
└─ prefix ──┘└──── 32 base64url characters ────┘
```

- **Format** — `coda_sk_live_` plus 24 random bytes, base64url. One format for
  every product; the scope decides what it opens.
- **Storage** — `sha256(pepper ‖ raw)`. The pepper is a server-only secret
  shared by the dashboard, the website and the TTS gateway. All three must hold
  the same value byte for byte; a mismatch does not raise an error, it simply
  makes every key read as invalid.
- **Scopes** — `tts`, `agent`, `stt`, `llm`. Keys issued before scoping existed
  carry `{tts}` and gained nothing.
- **Revocation** — the row is kept and `revoked_at` stamped, so past usage stays
  attributable. Revocation is immediate for new requests. Note the gateway
  caches key resolution briefly, so a revoked key can survive a few seconds.
- **Expiry** — an expired key resolves as invalid, exactly like a revoked one.

Treat a key like a password: server-side only, never in a browser bundle, a
mobile app, or a git repository.

---

## 4. Speech API — live now

Requires the `tts` scope.

**Base URL**

```
https://coda.eracoda.ai/api/tts
```

`www.coda.eracoda.ai`, `www.coda.eracoda.com` and `coda.eracoda.com` serve the
same path, all with valid certificates. Use `https` — over plain `http` the
redirect to TLS would downgrade `POST` to `GET` and hand back a web page
instead of audio.

`coda.eracoda.sa` and `www.coda.eracoda.sa` are **not** on this host yet; they
still resolve to the previous deployment (195.35.3.228) and have no certificate
here. Their nginx blocks exist and are inert until the DNS moves.

**Authentication**

```
Authorization: Bearer coda_sk_live_...
```

### `POST /v1/audio/speech`

Synthesise Arabic speech. OpenAI-compatible request shape.

| Field | Type | Notes |
| --- | --- | --- |
| `input` | string, required | Arabic text. Max **3000** characters. |
| `model` | string | `Coda-V2`. The only public model name. |
| `voice` | string | `nouf` (default) or `nawaf`. See voices below. |
| `speed` | float | 0.25 – 4.0. |
| `instructions` | string | Optional style hint. |

Returns `audio/wav` (16-bit PCM, mono, 48 kHz) with these headers:

| Header | Meaning |
| --- | --- |
| `x-model` | Always `Coda-V2`. |
| `x-request-id` | Quote this when reporting a problem. |
| `x-response-time-ms` | Server-side synthesis time. |

```bash
curl -X POST https://coda.eracoda.ai/api/tts/v1/audio/speech \
  -H "Authorization: Bearer $CODA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"Coda-V2","input":"مرحبا بكم في كودا","voice":"nouf"}' \
  --output speech.wav
```

With the OpenAI SDK — note the `/v1` suffix on the base URL, which the SDK
appends paths to:

```python
from openai import OpenAI

client = OpenAI(
    api_key=os.environ["CODA_API_KEY"],
    base_url="https://coda.eracoda.ai/api/tts/v1",
)

with client.audio.speech.with_streaming_response.create(
    model="Coda-V2",
    voice="nouf",
    input="مرحبا بكم في كودا",
) as response:
    response.stream_to_file("speech.wav")
```

Arabic diacritisation (tashkeel) is applied server-side before synthesis. Send
plain undiacritised Arabic; adding your own diacritics is allowed but
unnecessary.

### Other endpoints

| Endpoint | Auth | Returns |
| --- | --- | --- |
| `GET /v1/models` | none | `Coda-V2`, the only public identity. Deliberately unauthenticated — it discloses nothing a caller does not already know. |
| `GET /v1/voices` | key | `{"voices":["nouf"],"default":"nouf"}` — the default only; `nawaf` also synthesises. |
| `GET /v1/credits` | key | This key's remaining credits and the per-character cost. Internal keys report `{"metered":false,...}`. |
| `GET /health` | none | Service, model and version. |
| `GET /healthz` | none | Liveness probe. |

Because `/v1/models` needs no key, a `200` from it proves the gateway is
reachable — **not** that your key is valid. Use `GET /v1/voices` to check a key.

`/internal/*` and `/metrics` are blocked at the edge and always return `403`.

### Voices

Two voices synthesise: `nouf` (نوف, female — the default when `voice` is
omitted) and `nawaf` (نواف, male). Any other name is a `400`, so a typo fails
loudly instead of quietly returning the wrong speaker.

### Billing

One credit per input character, minimum one credit per request. Credits are
bought from the Coda dashboard. Internal keys are unmetered.

### Errors

| Status | Meaning |
| --- | --- |
| `400` | Malformed request, or an empty `input`. |
| `401` | Missing, malformed, revoked, or expired key. |
| `402` | Out of credits. |
| `403` | A blocked path (`/internal/`, `/metrics`). |
| `404` | Unknown `model`. This endpoint serves only `Coda-V2`. |
| `413` | `input` over 3000 characters. Also returned by the edge for a request body over 256 KB. |
| `429` | Per-key rate limit. Disabled on this deployment (`RATE_LIMIT_PER_MINUTE=0`). |
| `503` | **Edge rate limit, or a temporary upstream failure. Back off and retry.** |

Note `413` rather than `400` for over-long input, and `404` rather than `400`
for an unknown model name.

**`503` deserves care.** The edge allows ~30 requests/second per IP with a burst
of 60. All traffic from one server shares one bucket, so a batch job can trip it
while a browser never would. Retry with exponential backoff — and if you are
running an evaluation harness, make sure it treats `503` as *retry*, not as a
bad model response. Scoring a rate-limited `503` as a failed generation quietly
corrupts your results.

---

## 5. The voice agent on LiveKit

The agent is a **LiveKit worker**, not an HTTP endpoint. It registers with
LiveKit Cloud and waits for jobs. When a participant joins a room, LiveKit
dispatches the agent into that room, and it runs the pipeline:



with a speech gate, silence/turn detection, and interruption handling.

**Project:** `wss://coda-5x82qfeu.livekit.cloud`

### How a session starts today

1. A logged-in user on the Coda website calls `POST /api/connection-details`.
2. That route authenticates the session, checks the live-seconds budget, and
   mints a LiveKit access token whose TTL is capped to the remaining budget
   (hard ceiling 15 minutes). Fewer than 10 seconds left is refused with `402`.
3. The browser connects to LiveKit with that token using `livekit-client`.
4. LiveKit dispatches the agent (automatic dispatch — the worker registers with
   an empty `agent_name`, so it joins any room in the project).
5. The agent meters the call every 5 seconds against the authoritative Supabase
   budget and ends the session when it runs out.

### The token contract

Everything the agent needs to bill and configure a session travels in the signed
LiveKit token. This is the interface any future gateway must satisfy.

| Field | Where | Purpose |
| --- | --- | --- |
| `identity` | `u_<supabaseUserId>` | Who to bill. Taken from the identity, not metadata, because the identity is signed and cannot be altered client-side. |
| `metadata.userId` | JSON | Fallback for the above. |
| `metadata.liveSecondsRemaining` | JSON | Budget hint. Advisory only — the agent re-reads the authoritative value from Supabase and tightens to whichever is smaller. |
| `metadata.voice` | JSON | `female` (default) or `male`. |
| `metadata.apiKeyId` | JSON | **The key that opened the session.** Set only by the gateway; the website leaves it unset. Validated as a UUID before use. |
| `ttl` | Token | `min(remainingSeconds, 900)`. |

The budget hint is deliberately not trusted. It is a snapshot from the moment
the token was minted, so two sessions opened together would each believe they
owned the whole budget. The agent always re-checks.

### Metering

Every 5 seconds the agent calls:

```sql
consume_quota(p_user, 'live', <seconds>, p_api_key)
```

which decrements `usage_counters.live_seconds_used`, writes a row to
`usage_events`, and returns the authoritative remainder. When `p_api_key` is
present the usage is attributed to that key, which is what makes the dashboard's
per-key minutes work. Website sessions pass nothing and stay attributed to the
user alone.

Metering fails open: if Supabase is unreachable the call continues unbilled,
because accounting must never drop a live conversation.

### Running your own agent against this project

To point a worker at the same LiveKit project:

```bash
LIVEKIT_URL=wss://coda-5x82qfeu.livekit.cloud
LIVEKIT_API_KEY=...
LIVEKIT_API_SECRET=...
AGENT_PORT=2953          # local health port, must be free on the host
```

```bash
uv sync
.venv/bin/python agent.py download-files   # turn detector + VAD models
.venv/bin/python agent.py start
```

**Automatic dispatch is a hazard with more than one worker.** With an empty
`agent_name`, every registered worker is eligible for every room in the project,
so two deployments pointed at the same project will fight over jobs. Before
running a second agent, give each one an explicit `agent_name` and have the
token request it by name.

---

## 6. The realtime gateway — designed, not built

The intended shape for reaching the agent with an API key, so integrations can
be written against it:

```http
POST /v1/realtime/sessions
Authorization: Bearer coda_sk_live_...

→ 200 { "session_id": "...", "ws_url": "wss://...", "client_secret": "...",
        "expires_at": "..." }
```

```
wss://.../v1/realtime?session=<client_secret>     # browsers
wss://.../v1/realtime   Authorization: Bearer ... # server to server
```

The API key is a **session-minting** credential, never a streaming one. It buys
a short-lived token; the token does the streaming. That keeps the long-lived
secret on a server while still allowing a browser to open a session, and it is
the same split OpenAI Realtime and Gemini Live both use.

Admission checks at mint time: key valid, not revoked, not expired, `agent` in
scopes, budget ≥ 10 seconds, and open sessions for that key below its
`max_concurrent_sessions`.

Two things the gateway needs that do not exist yet: a `live_sessions` table to
count what is open (nothing tracks this today, so the concurrency ceiling is
recorded but unenforced), and the WebSocket ↔ LiveKit audio bridge itself.

---

## 7. Usage and limits

**Per-key minutes** — `/admin/keys` shows agent minutes over the last 30 days
and for all time, per key. Minutes are computed in the database, from
`usage_events` rows attributed to that key.

**Quotas are per owner, not per key.** Several keys owned by one account share
that account's budget. Defaults: free = 300 agent seconds per period, pro =
36 000. Override per user under **Users → limits**, which the dashboard reads
and writes in minutes while the database stores seconds.

**Concurrency** is per key (`max_concurrent_sessions`), and is enforced by the
gateway — which means not yet.

---

## 8. Troubleshooting

| Symptom | Cause |
| --- | --- |
| `401` on every request with a key you just issued | Pepper mismatch between the dashboard and the gateway. Compare `API_KEY_PEPPER` across `/etc/coda-admin/admin.env`, `coda-next/.env.local` and `TTS-Endpoint-api/.env`. |
| `401` after revoking, then briefly working | Key resolution is cached for a few seconds. It settles on its own. |
| `503` bursts under load | Edge rate limit, not your key. Back off; see section 4. |
| Agent key opens nothing | Expected. The realtime gateway is not built — section 1. |
| A `POST` returns HTML instead of audio | You used `http://`, and the redirect to `https` downgraded the method. Always call `https`. |
| Per-key minutes read 0 | Correct until agent traffic exists with a key attached. The website does not attach one. |

---

## 9. Operator reference

| Item | Value |
| --- | --- |
| Admin dashboard | `https://www.coda.eracoda.ai/admin/keys` |
| Speech API | `https://coda.eracoda.ai/api/tts` |
| LiveKit project | `wss://coda-5x82qfeu.livekit.cloud` |
| Key table | `public.api_keys` (`scopes`, `max_concurrent_sessions`, `expires_at`) |
| Usage | `public.usage_events` (`kind='live'`, `amount` in seconds, `api_key_id`) |
| Audit | `private.admin_audit_log` (`api_key.created`, `api_key.revoked`) |
| RPCs | `admin_create_api_key`, `admin_revoke_api_key`, `admin_list_api_keys`, `admin_user_id_for_email` — all `service_role` only |
| Services | `coda-admin-frontend` :8092 · `coda-tts` :8011 · `coda-next-frontend` :8090 · `coda-agent` :2953 |

Related: `ADMIN_DEPLOYMENT.md` (dashboard deployment), `RESTART_SERVICES.md`
(services and rebuilds).
