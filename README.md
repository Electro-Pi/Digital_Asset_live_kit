# 🎙️ Sera with MCP (Model Context Protocol)

**Sera (سيرا)** is an enterprise-grade, real-time Arabic voice assistant built with **LiveKit** and integrated with a remote **FastMCP (Model Context Protocol)** server.

The system delivers low-latency conversational AI by combining speech recognition, LLM reasoning, semantic caching, CRM integration, and streaming text-to-speech. It is optimized for the **Saudi Najdi dialect** and designed for production deployments.

---

# 🏗️ Architecture

The voice pipeline is fully event-driven and optimized for real-time WebRTC communication.

```text
User Speech
      │
      ▼
LiveKit + Silero VAD
      │
      ▼
Speech-to-Text
      │
      ▼
CRM Detector
      │
      ├──────────────► CRM Request
      │                     │
      │                     ▼
      │               FastMCP Server
      │
      ▼
Semantic Cache
      │
      ├──────── Cache Hit ─────► Response
      │
      ▼
Large Language Model
      │
      ▼
Text Processing
      │
      ▼
Text-to-Speech
      │
      ▼
LiveKit Audio Stream
```

---

# ⚡ Pipeline Overview

1. **Voice Activity Detection**
   - LiveKit captures microphone audio.
   - Silero VAD detects speech boundaries.

2. **Speech Recognition**
   - Audio is transcribed using a self-hosted Cohere Transcribe model.

3. **Intent Routing**
   - `is_crm_action()` determines whether the request requires live CRM data.
   - General questions are first checked against the semantic cache.

4. **Semantic Cache**
   - Cached responses are immediately returned.
   - The LLM receives a system override instructing it to speak the cached answer naturally in Najdi Arabic.

5. **CRM Execution**
   - CRM-related requests bypass the cache.
   - The agent plays a short typing sound while waiting for backend responses.
   - Requests are executed through the FastMCP server.

6. **Response Generation**
   - Markdown and formatting artifacts are removed.
   - Clean text is passed into the TTS pipeline.

---

# 🚀 Getting Started

## 1. Synchronize Dependencies

```bash
uv sync --active
```

---

## 2. Download Required Assets

Downloads local media files, testing resources, and model dependencies.

```bash
python agent.py download-files
```

---

## 3. Start the Development Agent

```bash
python agent.py dev
```

---

# 🔧 Model Configuration

Model endpoints can be modified inside the `my_agent()` function in `agent.py`.

Example configuration:

```python
session = AgentSession(
    stt=STTWithTemperature(
        base_url="http://<stt-server>/v1",
        model="cohere-transcribe",
        api_key="your-api-key",
        language="ar",
        temperature=0.0,
    ),

    llm=openai.LLM(
        base_url="http://<llm-server>/v1",
        model="gemma4",
        api_key="your-api-key",
    ),

    tts=OpenAITTSWithSpeechExtraBody(
        speech_extra_body=najdi_omnivoice_speech_extra_body(),
        base_url="http://<tts-server>/v1",
        api_key="your-api-key",
        response_format="pcm",
        speed=1.1,
    ),

    vad=silero.VAD.load(),
    turn_detection=MultilingualModel(),
)
```

---

# 🧩 Plugin Overview

## `crm_detector.py`

**Purpose**

Determines whether an incoming request requires live CRM access.

**Responsibilities**

- Detect ticket numbers
- Detect customer IDs
- Detect booking requests
- Detect order tracking requests
- Detect sensitive customer information

CRM requests bypass the semantic cache and are executed directly through FastMCP.

---

## `semantic_cache.py`

**Purpose**

Reduces latency and LLM inference costs.

**How it Works**

- Generates embeddings using FastEmbed
- Searches Qdrant
- Returns cached answers for semantically similar questions
- Avoids unnecessary LLM execution

---

## `openai_stt_with_temperature.py`

**Purpose**

Extends the default LiveKit STT implementation.

**Features**

- Forces deterministic transcription
- Sets `temperature=0.0`
- Reduces hallucinations for dialectal Arabic

---

## `openai_tts_with_extra_body.py`

**Purpose**

Extends the OpenAI-compatible TTS client.

**Features**

- Supports custom `speech_extra_body`
- Passes dialect-specific synthesis parameters
- Controls speaking style, prosody, and emotion

---

# 📂 Project Structure

```text
Digital_Asset_live_kit/
├── data/
│   └── office_low.wav
│
├── src/
│   ├── config/
│   │   └── omnivoice_najdi_tts.py
│   │
│   ├── plugins/
│   │   ├── crm_detector.py
│   │   ├── semantic_cache.py
│   │   ├── openai_stt_with_temperature.py
│   │   └── openai_tts_with_extra_body.py
│   │
│   └── prompts/
│       └── agent_prompt.py
│
├── agent.py
├── pyproject.toml
├── uv.lock
└── Dockerfile
```

---

# 🛠️ Technology Stack

| Category | Technology |
|----------|------------|
| Voice Transport | LiveKit |
| Streaming | WebRTC |
| VAD | Silero |
| Speech-to-Text | Cohere Transcribe |
| LLM | Gemma 4 |
| Text-to-Speech | OpenAI-Compatible TTS |
| Vector Database | Qdrant |
| Embeddings | FastEmbed |
| Tool Calling | FastMCP |
| Package Manager | uv |
| Deployment | Docker |

---

# 📄 License

This repository is intended for internal development and production deployment. Update this section with your preferred open-source or proprietary license before public release.
