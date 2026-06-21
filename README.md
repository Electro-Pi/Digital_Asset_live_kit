Here is the updated, production-ready `README.md` file, focused entirely on the core Digital Asset framework and stripped of the specific Sera branding.

```markdown
# 🎙️ Digital Asset Project

An enterprise-grade real-time Arabic voice agent framework optimized for the **Saudi Najdi dialect**. This repository provides a high-concurrency, low-latency architecture via LiveKit and advanced model orchestration, designed to power humanoid virtual assistants capable of complex dialectal interactions and real-time backend tool calling.

---

## 🏗️ Architecture Overview

The Digital Asset pipeline operates on three interconnected blocks designed for real-time WebRTC constraints and dialectal sovereignty:

* **Transcription (STT):** Powered by `cohere/transcribe` for state-of-the-art parsing of complex regional Saudi accents.
* **Reasoning & Routing (LLM):** Utilizes `gemma-4-26B-A4b` for deep logic, strict adherence to system prompts, and seamless function calling (FastMCP).
* **Speech Synthesis (TTS):** Leverages `Qwen3-TTS` and `Higgs-Audio-TTS-v3-4B` with continuous batching and custom fine-tuning for authentic Najdi dialect prosody.

### Core Components
* **LiveKit SDK & WebRTC:** Manages bidirectional audio, Voice Activity Detection (VAD), and turn detection with millisecond precision.
* **FastMCP Client:** Connects securely to external CRM database backends (via tools like `sera_chat`) for real-time customer data retrieval, complaint filing, and ticket tracking.
* **Semantic Cache Layer:** Uses Qdrant & FastEmbed to instantly return cached answers for general queries, bypassing LLM compute overhead.
* **CRM Detection Filter:** A routing script that instantly identifies sensitive or dynamic data (e.g., National IDs, complaint numbers) and routes them directly to the backend database, bypassing the cache.

---

## ⚙️ Prerequisites

Before you begin, ensure you have the following installed:
* **Python:** 3.12 or higher
* **uv:** The ultra-fast Python package installer and resolver
* **Docker:** For containerized deployment
* **LiveKit CLI:** For deploying to the LiveKit Cloud

---

## 🚀 Installation & Setup

**1. Clone the repository**
```bash
git clone [https://github.com/Electro-Pi/Digital_Asset_live_kit.git](https://github.com/Electro-Pi/Digital_Asset_live_kit.git)
cd Digital_Asset_live_kit

```

**2. Install dependencies**
Utilize `uv` to securely sync your environment based on the exact lockfiles provided:

```bash
uv sync

```

**3. Configure Environment Variables**
Create a `.env.local` file in the root directory and add your service credentials:

```ini
# LiveKit Connection Settings
LIVEKIT_URL="wss://your-livekit-server-url"
LIVEKIT_API_KEY="your-api-key"
LIVEKIT_API_SECRET="your-api-secret"

# Qdrant Database Configuration (Semantic Cache)
QDRANT_URL="your-qdrant-database-endpoint"
QDRANT_API_KEY="your-qdrant-api-key"

# Selected Model API Endpoints & Keys
STT_API_KEY="your-cohere-transcribe-key"
LLM_API_KEY="your-gemma-4-endpoint-key"
TTS_API_KEY="your-tts-model-key"

```

---

## 💻 Running the Agent Locally

You can run the agent locally for testing or connect it to a development LiveKit sandbox.

**Option A: Console Mode (Quick Text Test)**
Ideal for verifying routing, tool calls, and LLM responses without full audio initialization.

```bash
uv run agent.py console

```

**Option B: Development Mode**
Connects directly to your configured LiveKit room for full bidirectional audio testing.

```bash
uv run agent.py dev

```

---

## ☁️ Deployment

This project utilizes a multi-stage Dockerfile optimized for a lightweight Debian environment.

To deploy the containerized agent directly to LiveKit Cloud, simply run:

```bash
lk agent deploy

```

---

## 📂 Directory Structure

```text
Digital_Asset_live_kit/
├── Dockerfile                  # Multi-stage production container build
├── pyproject.toml / uv.lock    # Explicit dependency and environment mapping
├── agent.py                    # Main runtime orchestration script
└── src/
    ├── config/
    │   └── omnivoice_najdi_tts.py       # Dialect-specific TTS hyperparameters
    ├── plugins/
    │   ├── crm_detector.py              # Utterance pattern matching for CRM routing
    │   ├── semantic_cache.py            # Qdrant vector embedding lookup engine
    │   ├── openai_stt_with_temperature.py 
    │   ├── openai_tts_with_extra_body.py 
    │   ├── custom_f5_tts_plugin.py      
    │   ├── nv_tron_stt_plugin.py        
    │   └── qwen_tts_plugin.py           # VLLM / Continuous batching bridge
    ├── prompts/
    │   └── agent_prompt.py              # Core behavioral logic & Najdi governance
    └── utils/
        └── text_processing.py           # Markdown/numeral sanitization for fluid TTS

```

```

```
