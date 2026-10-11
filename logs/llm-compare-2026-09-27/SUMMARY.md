# LLM log comparison — 2026-09-27

## Which LLM ran which call

| Time (UTC+3 wall / journal local) | Room | LLM | Notes |
|---|---|---|---|
| Sep 27 11:03:39 | `room-1790507018946` | **coda-llm** | TEST endpoint 180.189.55.43:37835 |
| Sep 27 11:03:58 | `room-1790507037441` | **coda-llm** | TEST endpoint 180.189.55.43:37835 |
| Sep 27 11:04:28 | `room-1790507067996` | **coda-llm** | TEST endpoint 180.189.55.43:37835 |
| Sep 27 11:08:26 | `room-1790507305854` | **coda-llm** | TEST endpoint 180.189.55.43:37835 |
| Sep 27 11:10:47 | `room-1790507445317` | **coda-llm** | TEST endpoint 180.189.55.43:37835 |
| Sep 27 11:11:40 | `room-1790507496206` | **coda-llm** | TEST endpoint 180.189.55.43:37835 |
| Sep 27 11:15:19 | `room-1790507718445` | **Coda-V2** | PRODUCTION coda.eracoda.ai |
| Sep 27 11:16:54 | `room-1790507813236` | **coda-llm** | TEST endpoint 180.189.55.43:37835 |

Also included: previous-evening production reference `room-1790464710628` (2026-09-26 23:18) with **Coda-V2**.

## Failures observed under coda-llm

From the longest coda-llm sessions (`room-1790507067996`, `room-1790507305854`, `room-1790507813236`):

1. **Tool calls written as XML text, not executed** — repeated `Dropped an unparseable tool call written as text` for `<tool_call><function=search_meals>...`. No real `Tool search_meals` / `show_meal` ran.
2. **Empty reply → fallback speech** — after each dropped tool leak she says معذرة / ما وصلني كلامك / عذرا قولي وش تبي.
3. **No catalog navigation** — unlike Coda-V2 calls which show `Turn tools: [('search_meals'...)]` then `Tool show_meal ... ok=True`.
4. **Hallucinated / off-script selling talk** — TTS previews invent dishes and sizes without tool evidence (shawarma for 4, etc.).

## What Coda-V2 did correctly in the same window

`room-1790507718445` (11:15, Coda-V2): user asked for مشاوي → real `search_meals(query='وجبة مشاوي فردية')` → `show_meal majalis-almashawi-offer-1 ok=True`.

Previous evening `room-1790464710628` (Coda-V2): search كبسة حاشي → show_meal → respond_to_offer accepted with cola addon.

## Files

Directory: `coda-jahez-agent-backend/logs/llm-compare-2026-09-27/`

- `00-call-index.txt` — every Call starting line with model=
- `ACTIONS__{LLM}__{room}.log` — transcripts/tools/errors only
- `FULL__{LLM}__{room}__pid*.log` — full pid journal (minus VAD/TTS noise)
- `MASTER_ACTIONS_BY_LLM.log` — all ACTIONS concatenated
- `SUMMARY.md` — this file
