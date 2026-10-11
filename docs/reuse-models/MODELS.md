# Reuse this agent's models in another project

This pack is the hosted **Coda speech gateway** that سلمى uses today: one
OpenAI-compatible host, one API key, three roles (LLM / TTS / STT).

It does **not** include LiveKit, the Jahez catalog, or restaurant tools. Copy
this folder and call the HTTP APIs from any stack.

## Hosts and auth

| Role | Env var | Production value | Client appends |
|---|---|---|---|
| TTS base | `TTS_SERVER_URL` | `https://coda.eracoda.ai/api/tts` | `/v1/audio/speech` |
| STT base | `STT_BASE_URL` | `https://coda.eracoda.ai/api/tts` | `/v1/audio/transcriptions` |
| LLM base | `LLM_BASE_URL` | `https://coda.eracoda.ai/api/tts/v1` | `/chat/completions` |

All three currently use model id `Coda-V2`. TTS voice is `nouf`.

Auth on every request:

```
Authorization: Bearer <your coda_sk_… key>
```

Use your own key. Do not copy keys out of this repo's `.env.local` into git.

**URL shape is the usual footgun:** TTS/STT bases have **no** `/v1`. The LLM
base **has** `/v1`. A trailing `/v1` on TTS/STT is stripped by this agent's
plugins so you do not get `/v1/v1/audio/speech`.

## Discovery

```bash
export CODA_API_KEY=coda_sk_REPLACE_ME
export CODA_TTS=https://coda.eracoda.ai/api/tts

curl -sS -H "Authorization: Bearer $CODA_API_KEY" "$CODA_TTS/v1/models"
curl -sS -H "Authorization: Bearer $CODA_API_KEY" "$CODA_TTS/v1/audio/voices"
```

Voices this agent knows: `nouf` (production female), `hoda`, `nawaf`,
`default`. Unknown `voice` → HTTP 400. Unknown `model` → HTTP 404.

---

## LLM — chat completions

```
POST {LLM_BASE_URL}/chat/completions
Authorization: Bearer <key>
Content-Type: application/json
```

```json
{
  "model": "Coda-V2",
  "messages": [
    {"role": "system", "content": "أجب بالعربي باختصار."},
    {"role": "user", "content": "وش عندكم بيتزا؟"}
  ],
  "temperature": 0.6,
  "max_tokens": 400
}
```

With tools (same shape this agent sends):

```json
{
  "model": "Coda-V2",
  "messages": [{"role": "user", "content": "أبغى بيتزا"}],
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "search_meals",
        "description": "Search the menu.",
        "parameters": {
          "type": "object",
          "properties": {"query": {"type": "string"}},
          "required": ["query"]
        }
      }
    }
  ],
  "tool_choice": "auto",
  "temperature": 0.6,
  "max_tokens": 400
}
```

Response is standard OpenAI: `choices[0].message` with `content` and optional
`tool_calls`.

```bash
curl -sS "$LLM_BASE_URL/chat/completions" \
  -H "Authorization: Bearer $CODA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Coda-V2",
    "messages": [{"role": "user", "content": "قل مرحبا"}],
    "temperature": 0.6,
    "max_tokens": 80
  }'
```

OpenAI SDK (Python):

```python
from openai import OpenAI
client = OpenAI(
    base_url="https://coda.eracoda.ai/api/tts/v1",
    api_key="coda_sk_REPLACE_ME",
)
print(client.chat.completions.create(
    model="Coda-V2",
    messages=[{"role": "user", "content": "قل مرحبا"}],
).choices[0].message.content)
```

---

## TTS — speech

```
POST {TTS_SERVER_URL}/v1/audio/speech
Authorization: Bearer <key>
Content-Type: application/json
```

What this agent actually sends:

```json
{
  "model": "Coda-V2",
  "input": "يا هلا، معك سلمى من لقمة.",
  "voice": "nouf",
  "language": "Arabic",
  "response_format": "pcm",
  "stream": true,
  "stream_format": "audio",
  "seed": 1234
}
```

| Field | Required | Notes |
|---|---|---|
| `model` | yes | `Coda-V2` |
| `input` | yes | Arabic text. Empty string → 400. |
| `voice` | yes | `nouf` in production. Use `default` only when cloning. |
| `language` | no | Agent sends `"Arabic"`. |
| `response_format` | yes | `pcm` |
| `stream` | yes | `true` — chunked body, first audio ~75ms |
| `stream_format` | yes | `audio` |
| `seed` | no | `1234` for a stable speaker |
| `ref_audio` / `ref_text` | no | Clone path: `voice=default` + reference wav + transcript |

Response body is raw **48 kHz, 16-bit little-endian, mono PCM**. No WAV
header. Play or wrap it yourself.

Do **not** send the server `speed` field. On this gateway it is a resampler
and it changes pitch. Pace locally if you need it.

Unknown extra JSON fields are ignored. A stale voice name is the loud
failure (400), not a silent fallback to `default`.

```bash
curl -sS "$TTS_SERVER_URL/v1/audio/speech" \
  -H "Authorization: Bearer $CODA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Coda-V2",
    "input": "يا هلا",
    "voice": "nouf",
    "language": "Arabic",
    "response_format": "pcm",
    "stream": true,
    "stream_format": "audio",
    "seed": 1234
  }' --output speech.pcm

# 48 kHz s16le mono
ffplay -f s16le -ar 48000 -ac 1 speech.pcm
```

Voice clone (optional):

```json
{
  "model": "Coda-V2",
  "input": "النص المراد نطقه",
  "voice": "default",
  "language": "Arabic",
  "response_format": "pcm",
  "stream": true,
  "stream_format": "audio",
  "ref_audio": "<base64 or server-accepted audio>",
  "ref_text": "transcript of the reference clip"
}
```

---

## STT — transcription

```
POST {STT_BASE_URL}/v1/audio/transcriptions
Authorization: Bearer <key>
Content-Type: multipart/form-data
```

| Field | Value |
|---|---|
| `model` | `Coda-V2` |
| `language` | `ar` |
| `response_format` | `json` |
| `stream` | `false` (one-shot) or `true` (SSE) |
| `file` | WAV, `audio/wav` — **16 kHz, 16-bit, mono** |

One-shot JSON:

```json
{ "text": "أبغى بيتزا" }
```

Streaming (`stream=true`) is Server-Sent Events. Lines look like:

```
data: {"choices":[{"delta":{"content":"أبغى"}}]}
data: [DONE]
```

```bash
curl -sS "$STT_BASE_URL/v1/audio/transcriptions" \
  -H "Authorization: Bearer $CODA_API_KEY" \
  -F model=Coda-V2 \
  -F language=ar \
  -F response_format=json \
  -F stream=false \
  -F file=@clip.wav
```

---

## Minimal env for another project

```
CODA_API_KEY=coda_sk_REPLACE_ME
TTS_SERVER_URL=https://coda.eracoda.ai/api/tts
TTS_MODEL=Coda-V2
TTS_VOICE=nouf
LLM_BASE_URL=https://coda.eracoda.ai/api/tts/v1
LLM_MODEL=Coda-V2
STT_BASE_URL=https://coda.eracoda.ai/api/tts
STT_MODEL=Coda-V2
```

Copy `.env.example` in this folder. The `clients/` scripts read those names.

## What this pack is not

- LiveKit room tokens / worker registration (`LIVEKIT_*`) — not needed to call
  the models.
- Jahez menu tools (`search_meals`, `add_to_cart`, …) — those are this
  restaurant agent, not the model host.
- Server-side `speed` on TTS — do not send it.

## Local alternatives (not production)

This repo's plugins also talk to unauthenticated local vLLM:

| Role | Typical local URL | Model id |
|---|---|---|
| TTS | `http://localhost:16326` | `VoxCPM2` |
| LLM | `http://127.0.0.1:8001/v1` | `gemma4` |
| STT | `http://localhost:8000` | `CohereLabs/cohere-transcribe-arabic-07-2026` |

Same paths (`/v1/audio/speech`, `/v1/chat/completions`,
`/v1/audio/transcriptions`). Bearer token only if that server was started
with `--api-key`.
