# Model reuse pack

Copy this whole folder into another project. It is everything needed to call
the same hosted LLM, TTS, and STT that the Jahez voice agent uses.

```
docs/reuse-models/
  README.md              this file
  MODELS.md              hosts, paths, payloads, curl
  .env.example           env names (placeholders only)
  config.json            non-secret hosts / models / formats
  clients/
    requirements.txt
    _env.py              shared env loader
    llm_chat.py
    tts_speak.py
    stt_transcribe.py
```

## Setup in the other project

```bash
cp -r docs/reuse-models /path/to/other-project/coda-models
cd /path/to/other-project/coda-models
cp .env.example .env
# put YOUR coda_sk_… key in CODA_API_KEY
python3 -m venv .venv && . .venv/bin/activate
pip install -r clients/requirements.txt
```

## Smoke tests

```bash
python clients/llm_chat.py "قل مرحبا بجملة واحدة"
python clients/tts_speak.py "يا هلا، معك سلمى من لقمة." --out hello.wav
python clients/stt_transcribe.py hello.wav
```

`MODELS.md` has the exact HTTP contracts if you are not using Python.

Use your own Coda key. Do not commit `.env` or copy keys from this repo's
`.env.local`.
