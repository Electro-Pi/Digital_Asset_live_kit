# Luqma voice agent (سلمى)

سلمى is a voice sales agent for the Luqma food-ordering app. She speaks plain,
professional Saudi Arabic, finds dishes, drives the caller's screen, sells
extras and complements, and places the order. Every call opens with the same
line, «حياك الله، معاك سلمى من لقمة. إيش أقدر أساعدك فيه اليوم؟»
(`flow_say.GREETING_SAY_AR`). She runs on
[LiveKit Agents](https://docs.livekit.io/agents/) (1.8) as a cascaded
pipeline:

```
caller mic ─► BVC noise cancellation ─► Silero VAD ─► Coda ASR ─► turn detector
          ─► LLM (Coda-V2 / instance / Gemini) + 8 tools ─► VoxCPM TTS ─► caller
                                   │
                                   └── LiveKit RPC ─► the app in the caller's browser
```

## How the agent is built

| Piece | Where | What it does |
| --- | --- | --- |
| Entrypoint | `agent.py` | Builds the voice pipeline for one call and starts the session. Wiring only. |
| Shopping agent | `src/voice_agent/assistant.py` | سلمى, with 8 tools. |
| Tools | `src/voice_agent/*_tools.py` | `search_meals`, `show_meal`, `respond_to_offer`, `get_cart`, `update_cart`, `open_page`, `saved_meals`, `checkout`. |
| Checkout | `src/voice_agent/checkout.py` | A LiveKit `AgentTask` with its own instructions and tools: collects details, reads the summary, places the order. |
| LLM node and guardrails | `src/voice_agent/base.py` | Shared by the agent and the checkout task. |
| Search | `src/search.py` | Typed filters and BM25 ranking over the catalog. |
| Catalog | `src/catalog.py` | Loading, spoken forms (numbers, names), pairing, upgrades. |
| Offer facts | `src/offers.py` | Selling points, extras, bigger sizes, meal deals, complements, delivery time, as facts. Upsells above the caller's budget are not offered. |
| Session state | `src/session_state.py` | Open offers, cart memory, checkout details for one call. |
| Settings and rules | `src/config.py` | Every environment setting and business rule. |
| Prompt | `src/prompts/agent_prompt.py` | Persona, tool rules, the sales playbook (`SALES_PLAYBOOK`), dialect and pronunciation. |
| Sales prompt optimiser | `scripts/optimize_sales_prompt.py` | GEPA (the engine behind `dspy.GEPA`) rewrites `SALES_PLAYBOOK`, scored on live sales conversations. See below. |

### Principles

- **The model decides, code validates.** The LLM picks every tool call. Code
  checks the call against the tool's schema (`src/tool_guard.py`) and the
  order's state (inside each tool), and returns an actionable error when a
  call cannot run. Nothing reads the caller's transcript to pick, add or veto
  a tool.
- **Offers have ids.** Every question she asks («تبيها؟», «تبي إضافات؟», the
  bigger size, the complement, «نثبت الطلب؟») is an offer with an id such as
  `pitch:shawarma-1`. The model answers with `respond_to_offer(offer_id,
  accepted)`, and the code checks that the offer is open. A «ايه» can only
  accept the offer the model names.
- **Tools return facts.** Names, spoken prices and selling points, never
  scripts or instructions. How she phrases a sale lives in the prompt. Only
  the greeting (`session.say` in `agent.py`), the order summary and the order
  confirmation (`say_exact`) are spoken word for word; the last two carry what
  the caller is agreeing to pay.
- **A recommendation request gets three questions first.** When he asks her to
  recommend («وش ترشحين», «جوعان», «أبي شي صحي»), `search_meals` hands back
  one `narrowing_question` per turn: his taste (two options from the catalog
  «or something else»), how many people, his budget, skipping any he already
  said. The session remembers the answers (`Discovery` in
  `src/session_state.py`) and the last search filters on all three and opens
  the recommendation. A group's budget is for the whole table
  (`group_budget`: as many as feed them must fit). A named dish («أبي برجر»)
  gets no questions.
- **Writes are gated and cannot be interrupted.** `place_order` runs only
  after the summary was read and the caller said yes (`summary_confirmed`).
  Every tool that changes the cart or the order calls
  `disallow_interruptions()`.
- **The LLM node has no side effects.** LiveKit may run it speculatively
  (preemptive generation). It streams her reply as it is generated and holds
  back only a sentence that claims an action no tool performed («حطيتها لك»,
  «ثبت طلبك»). Guardrails (ASR noise, prompt-injection attempts, discount
  demands) run once per committed turn in `on_user_turn_completed`.
- **Failures are honest.** A screen that did not respond is reported as
  `ok: false`. `UI_MOCK_ON_FAILURE=1` exists for demos without a frontend, and
  marks every result it fakes with `"mocked": true`.

## Data

| File | Owner | Purpose |
| --- | --- | --- |
| `resturant's-catalog/catalog.json` | the app | Byte-for-byte copy of `jahez-frontend/data/catalog.json`. Refresh with `python scripts/sync_catalog.py`. The agent logs an error at startup if the copies differ. |
| `resturant's-catalog/meal_attributes.json` | this repo | Dish type (main, side, salad, soup, drink, dessert, grocery), protein and tags for every meal. Written by `python scripts/annotate_catalog.py`. Rows marked `"reviewed": true` are hand-corrected and never overwritten. |
| `data/search_vocab.json` | this repo | Words the search reads in a query: stopwords, filter words («خفيف», «أرخص»), dish-type words («حلا», «سلطة»), protein and cuisine cues. |
| `data/operations.json` | this repo | Delivery zones, pickup branches, spoken arrival times. |
| `data/known_customers.json` | runtime | Returning customers (phone → name, last order). |

After changing the catalog, run `python scripts/sync_catalog.py`. It copies
the app's catalog and annotates any new meals. Then run the tests.

## Running

```bash
uv sync                                   # dependencies
uv run -m livekit.agents download-files   # VAD and turn-detector models
uv run agent.py dev                       # development worker
uv run agent.py start                     # production worker
```

Production runs as the systemd unit `jahez-agent.service` (`systemd/`), with
`.env.local` as its environment file.

## Configuration

Everything is read in `src/config.py`, from `.env.local` or the environment.
The ones you are most likely to change:

| Variable | Default | Meaning |
| --- | --- | --- |
| `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | | LiveKit project. |
| `LIVEKIT_AGENT_NAME` | `luqma` | The name the frontend's token dispatches to. |
| `LLM_PROVIDER` | `coda` | `coda`, `instance` or `gemini`; see `src/llm_backend.py`. |
| `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` | | Coda-V2 gateway. |
| `STT_BASE_URL`, `STT_MODEL`, `STT_API_KEY` | | Coda ASR. |
| `TTS_SERVER_URL`, `TTS_API_KEY`, `TTS_MODEL`, `TTS_VOICE` | | VoxCPM TTS. |
| `STT_VAD_ACTIVATION_THRESHOLD` | `0.65` | Silero VAD sensitivity. |
| `STT_FOREGROUND_GATE` | `1` | Loudness gate in front of the ASR. Every rejection is logged with its levels; `0` turns it off. |
| `TURN_DETECTOR_VERSION` | `v1-mini` | Audio end-of-turn model. It runs locally and supports Arabic. |
| `ENDPOINTING_MIN_DELAY` / `ENDPOINTING_MAX_DELAY` | `0.8` / `2.5` | Seconds of silence before a turn closes (min when the turn detector says he is done, max when it says he is not). |
| `MAX_TOOL_STEPS` | `4` | Tool calls the model may chain in one turn. |
| `USER_AWAY_TIMEOUT` | `2700` | Seconds of caller silence before the session is marked away. |
| `UI_MOCK_ON_FAILURE` | `0` | Demo mode for runs without a frontend. |
| `HEALTH_MAX_KCAL_PER_PERSON`, `SOLO_UPGRADE_MAX_PRICE_RATIO`, `MAX_DECLINED_OFFERS`, … | | Business rules; see `BusinessRules` in `src/config.py`. |

### Switching the LLM

`LLM_PROVIDER` picks the reasoning backend and nothing else. The prompt, the
tools and the voice pipeline are shared:

| `LLM_PROVIDER` | Reads |
| --- | --- |
| `coda` (default) | `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` |
| `instance` | `INSTANCE_API_KEY`, `INSTANCE_BASE_URL`, `INSTANCE_MODEL`, `INSTANCE_REASONING_EFFORT`, `INSTANCE_TEMPERATURE` |
| `gemini` | `GEMINI_API_KEY`, `GEMINI_MODEL`, `GEMINI_REASONING_EFFORT`, `GEMINI_TEMPERATURE` |

## Tests

```bash
uv run pytest                          # everything offline: ~360 tests, seconds
RUN_LIVE_TESTS=1 uv run pytest -m live # the same flows against the real LLM
```

| File | Covers |
| --- | --- |
| `tests/test_search.py` | What a caller's words should find: named dishes, types, not-found, headcounts, filters, determinism. |
| `tests/test_voice_tools.py` | Every tool against a fake browser: read-only search, offers, extras, upgrades, cross-sell, cart, pages. |
| `tests/test_checkout.py` | The checkout task's gates: summary before order, his yes, phone, zones, pickup, leaving checkout. |
| `tests/test_llm_node.py` | Streaming, the claim guard, leaked tool calls, fallbacks, no side effects, guardrails. |
| `tests/test_full_flow.py` | A whole order through a real LiveKit `AgentSession` with a scripted LLM. |
| `tests/test_live_flow.py`, `tests/test_live_scenarios.py` | The same with the real model (`RUN_LIVE_TESTS=1`). |
| `tests/test_units.py` | Config, session state, offer facts, catalog data and sync, guardrails, prompt pins. |

### Optimising the sales playbook

`scripts/optimize_sales_prompt.py` scores سلمى on 20 short sales calls
(meal deal, budget, price objection, hurry, group, complaint, browsing,
«خلني أفكر», side questions, closing). Each call runs through the real
`Assistant` and its guards on the production model. The score combines hard
checks on the tools and cart, the voice rules, and a Gemini judge. GEPA
rewrites only `SALES_PLAYBOOK`; a person pastes the winner in after reading it.
dspy needs its own venv, because it upgrades `aiohttp`, which livekit pins:

```bash
uv venv --python 3.12 .venv-optim
uv pip freeze --python .venv/bin/python | grep -v '^-e' > /tmp/req.txt
uv pip install --python .venv-optim/bin/python -r /tmp/req.txt dspy
set -a; source .env.local; set +a
.venv-optim/bin/python scripts/optimize_sales_prompt.py baseline            # current prompt vs git HEAD
.venv-optim/bin/python scripts/optimize_sales_prompt.py optimize --budget 240
.venv-optim/bin/python scripts/optimize_sales_prompt.py baseline --skip-old --playbook logs/sales-optim/run-*/best_playbook.md
```

Keep `--parallel` at 6 or below: the Coda gateway also serves live speech.
