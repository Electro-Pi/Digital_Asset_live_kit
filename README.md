# KFSHRC Family Medicine — Arabic AI Patient Assistant

A real-time Arabic voice agent for a primary-care setting, built for the KFSHRC
Family Medicine Phase 1 Proof of Concept. Patients speak to it in Saudi Arabic;
it answers questions about symptoms, chronic conditions, screening and
prevention, and helps them judge how urgently they need to be seen.

Every clinical answer is grounded in retrieved passages from seven approved
guideline bodies — **USPSTF, AAFP, NICE, ACC/AHA, CDC, AAP, ACOG** — and the
agent is instructed to refer the patient to a human rather than answer when the
corpus has nothing relevant. It has no access to any medical record and stores
no patient data.

---

## How a turn works

```
mic → Cohere ASR (Arabic) → final transcript
        │
        ├─ 1. Query classifier (same gemma vLLM endpoint, ~90 ms)
        │     DIRECT   → greetings/thanks/meta, skip retrieval entirely
        │     RETRIEVE → anything health-related, including emergencies
        │
        ├─ 2. Retrieval (Gemini embed + Qdrant search, ~150 ms)
        │     runs concurrently with the classifier by default
        │
        └─ 3. gemma LLM with the evidence block appended → MOSS TTS
              judges urgency itself, under the prompt's three-level rules
```

The whole path from end-of-utterance to the LLM call is capped by
`RAG_TIMEOUT_MS` (700 ms). Blowing the budget drops the *evidence*, not the
turn — the prompt's "no evidence" rule then makes the agent refer the patient
instead of improvising.

Three design decisions worth knowing:

- **Urgency is the answering model's judgement, not a keyword filter.** A
  deterministic regex screen used to sit in front of the model and was removed:
  it matched any mention of a fall, so "my son fell down the stairs and scraped
  his knee" — squarely in the musculoskeletal scope — delivered a cardiac-arrest
  script. The prompt now defines three levels (call an ambulance / be seen
  today / just answer the question) and states plainly that over-escalation is
  itself a harm, because it frightens patients and trains them to discount the
  warning when it is real. `tests/test_retrieval_quality.py` asserts on the
  spoken answer in both directions.
- **Arabic queries hit an English corpus directly.** `gemini-embedding-001` is
  strongly multilingual, so no translation step is needed at query time.
  Ingestion additionally writes an Arabic summary onto every chunk, which is
  what the agent paraphrases aloud and what an Arabic-speaking clinician
  reviews.
- **Retrieval is skipped for social turns.** Most turns in a real consultation
  are not clinical questions; classifying first avoids spending an embedding
  call and 150 ms on "شكراً". The router sees the last two messages from either
  side, not just the patient's — a fragment like "ايه عندي سخونة" only reads as
  clinical next to the question it answers, and classifying it alone once sent a
  red, hot, febrile joint to the model with no guideline behind it.
- **The interview is capped, and the call has to end in an assessment.** The
  prompt allows at most two clarifying questions per complaint and then requires
  a closing answer: the likely explanation, what to do now, the signs that mean
  be seen sooner, and how soon otherwise. Without that cap the model asked a
  question every turn and closed with "راجع الطبيب", which is a consultation
  that cost the patient four answers and returned nothing.
  `test_consultation_ends_in_an_assessment_not_more_questions` replays a whole
  call and fails if it drifts back.

---

## Requirements

- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- Docker (for Qdrant)
- A Google API key with access to `gemini-embedding-001`
- Reachable vLLM endpoints for the Arabic ASR and the gemma LLM
- A MOSS TTS server with the `coda-2` voice registered

## Setup

```bash
uv sync
cp .env.example .env.local   # then fill in the values
docker compose up -d         # Qdrant on 127.0.0.1:6335
```

Ports are shifted off Qdrant's 6333/6334 defaults because an unrelated project
already owns those on the deployment host — same reason the agent runs on 2954.

## Building the guideline corpus

The agent **refuses to start** until this has been run: an empty index would
turn every clinical question into an ungrounded answer.

```bash
# 1. Download, extract and chunk all 104 source documents.
#    Caches raw HTML/PDF, so later stages never re-hit the publishers.
uv run python -m src.ingestion.cli fetch --source all

# 2. Add Arabic summaries and controlled-vocabulary topic tags
#    (runs on the existing gemma endpoint; public guideline text only).
uv run python -m src.ingestion.cli enrich --source all --concurrency 8

# 3. Embed and publish. Builds `medical_guidelines_v1`, verifies the count,
#    then repoints the `medical_guidelines` alias atomically.
uv run python -m src.ingestion.cli index

# 4. Write the provenance and scope-coverage report.
uv run python -m src.ingestion.cli report
```

Individual stages accept `--source uspstf|cdc|nice|acc_aha|aafp|acog|aap|all`.
To pull fresh copies from the publishers, add `--refresh` to `fetch`.

`retag` re-applies the catalogs' topic tags without calling the LLM or
re-embedding — useful after editing a source catalog, since topic tags are
payload only and never enter a chunk's vector:

```bash
uv run python -m src.ingestion.cli retag --source all --update-index
```

### Embedding quota — read this before building the corpus

**A paid Gemini key is required to index the full corpus.** The free tier caps
`embed_content` at **1,000 requests per day**
(`EmbedContentRequestsPerDayPerUserPerProjectPerModel-FreeTier`), and each
*text* counts as one request — batching does not exempt them. The corpus is
~2,300 chunks, so a free key cannot finish it in a day.

Two other free-tier behaviours worth knowing:

- There is a separate ~100-per-minute limit, and it rejects **bursts**: one
  request carrying 32 texts spends 32 units at once and trips it even when the
  per-minute average is fine. Hence `EMBEDDING_BATCH_SIZE=4` and
  `EMBEDDING_SECONDS_PER_ITEM=0.8`.
- On a 429 the API reports a `retryDelay` of ~30-60s regardless of which quota
  was hit, so a per-day exhaustion looks exactly like a per-minute one until
  you inspect the `quotaId`.

`index --resume` keeps whatever is already indexed, so the build can be run
across several days on a free key, or resumed after any interruption:

```bash
uv run python -m src.ingestion.cli index --resume
```

On a paid key, raise `EMBEDDING_BATCH_SIZE` and drop
`EMBEDDING_SECONDS_PER_ITEM` toward zero — the build then takes a few minutes.

Ad-hoc query check:

```bash
uv run python -m src.ingestion.cli search "متى أبدأ فحص سرطان القولون؟"
```

### Sources

Each publisher is a **curated catalog** in `src/ingestion/sources/`, not a
crawler. The PoC requires clinical experts to validate which documents the
assistant reads, and a reviewable list of titles and URLs is something a
physician can sign off on.

Two access constraints are worth recording:

- **ACC/AHA** full guideline texts in *Circulation* and *JACC* sit behind
  Cloudflare and are not machine-retrievable. The corpus uses the
  PMC-deposited 2019 primary-prevention guideline, ACC's public guideline
  summaries, and AHA/American Stroke Association patient-education pages.
- **NICE** content is UK-jurisdiction and free for non-commercial use with
  attribution. Every NICE chunk is tagged `jurisdiction=UK` and the licence
  note appears in the corpus report. If the PoC converts to a commercial
  engagement, NICE syndication terms need revisiting.

`knowledge/CORPUS_REPORT.md` lists every document with its URL, fetch date and
chunk count, plus coverage against the PoC's clinical scope.

## Running

```bash
uv run agent.py console   # terminal, no audio
uv run agent.py dev       # full voice agent against LiveKit
uv run agent.py start     # production
```

## Tests

```bash
uv run pytest tests/ -q
```

The unit suites (chunk provenance, prompt contract,
evidence formatting, log redaction) run anywhere.
`tests/test_retrieval_quality.py` is an integration suite — it needs Qdrant, a
built index and reachable Gemini/vLLM endpoints, and skips otherwise. It scores
routing and retrieval against 60 Arabic patient questions in
`tests/fixtures/golden_questions_ar.jsonl`, which doubles as a clinical review
artifact.

## Clinical review pack

```bash
uv run python -m src.eval.run_eval --out knowledge/EVAL.md
```

Produces one section per question showing the route taken, the exact guideline
passages retrieved with their source URLs, and the answer generated — the
artifact the PoC's clinical validation runs on.

---

## Layout

```text
agent.py                      LiveKit entrypoint, session wiring, billing
docker-compose.yml            Qdrant
src/
├── rag/
│   ├── config.py             all RAG settings, approved-publisher list
│   ├── models.py             GuidelineChunk + fail-closed provenance contract
│   ├── embedder.py           Gemini embeddings (query vs document task types)
│   ├── store.py              Qdrant client, versioned collections + alias swap
│   ├── classifier.py         DIRECT / RETRIEVE routing
│   ├── context.py            injection-hardened evidence block
│   └── retriever.py          per-turn orchestration under one deadline
├── ingestion/
│   ├── cli.py                fetch | enrich | index | report | search
│   ├── fetch.py              polite cached HTTP (robots.txt, crawl delays)
│   ├── extract.py            trafilatura → heading-scoped blocks, bs4 fallback
│   ├── chunker.py            ~300-token section-aware chunking
│   ├── enrich.py             Arabic summaries + topic tags
│   ├── index_build.py        embed → upsert → verify → promote alias
│   ├── taxonomy.py           controlled vocabulary mirroring the PoC scope
│   └── sources/              one curated catalog per publisher
├── prompts/medical_prompt.py grounding contract, safety rules, voice style
├── plugins/                  Cohere ASR, MOSS TTS
├── billing/                  Supabase live-minute metering
└── eval/run_eval.py          clinical review pack generator
```

## Deployment

Qdrant runs under `systemd/medical-coda-qdrant.service`; the agent itself runs
under the pre-existing `medical-coda-back.service` on this host. The agent unit
must be ordered after Qdrant, because the agent's fail-closed startup check
would otherwise crash-loop through a reboot.

```bash
sudo cp systemd/medical-coda-qdrant.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now medical-coda-qdrant
sudo systemctl restart medical-coda-back
```

## Privacy

`LOG_SENSITIVE_CONTENT` is `false` by default and should stay that way on any
deployed host. With it off, transcripts and conversation content are reduced to
character counts in the logs and provider errors are stripped of request
bodies. Patient utterances are health information and journald is not a place
for them.

At query time the patient's utterance is sent to the Gemini embedding API for
vectorisation. Nothing is stored there, and Google Cloud is on the PoC's
approved Saudi-compliant provider list, but it does leave the host — flagged
here because it is the one outbound path in the runtime.
