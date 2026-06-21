# 🎙️ Digital Asset Project

An enterprise-grade real-time Arabic voice agent framework optimized for the **Saudi Najdi dialect**. This repository provides a high-concurrency, low-latency architecture built with LiveKit and advanced model orchestration, designed to power humanoid virtual assistants capable of complex dialectal conversations and real-time backend tool calling.

---

# 🏗️ Architecture Overview

The Digital Asset pipeline consists of three interconnected components optimized for real-time WebRTC communication and dialect-specific performance.

- **Speech-to-Text (STT):** Powered by `cohere/transcribe` for accurate transcription of regional Saudi Arabic accents.
- **Reasoning & Routing (LLM):** Uses `gemma-4-26B-A4b` for conversational reasoning, strict system prompt adherence, and FastMCP tool calling.
- **Text-to-Speech (TTS):** Supports `Qwen3-TTS` and `Higgs-Audio-TTS-v3-4B` with continuous batching and custom fine-tuning for authentic Najdi speech synthesis.

## Core Components

- **LiveKit SDK & WebRTC**
  - Real-time bidirectional audio streaming
  - Voice Activity Detection (VAD)
  - Turn detection

- **FastMCP Client**
  - Secure CRM integration
  - Customer information retrieval
  - Complaint creation
  - Ticket tracking

- **Semantic Cache**
  - Built on **Qdrant** and **FastEmbed**
  - Returns cached responses for general questions
  - Reduces latency and LLM inference cost

- **CRM Detection Filter**
  - Detects requests containing dynamic or sensitive information
  - Routes CRM-related queries directly to backend tools
  - Bypasses the semantic cache when necessary

---

# ⚙️ Prerequisites

Before getting started, install the following:

- Python **3.12+**
- **uv** (Python package manager)
- Docker
- LiveKit CLI

---

# 🚀 Installation

## 1. Clone the repository

```bash
git clone https://github.com/Electro-Pi/Digital_Asset_live_kit.git
cd Digital_Asset_live_kit
```

## 2. Install dependencies

```bash
uv sync
```

## 3. Configure environment variables

Create a `.env.local` file in the project root.

```ini
# LiveKit
LIVEKIT_URL="wss://your-livekit-server-url"
LIVEKIT_API_KEY="your-api-key"
LIVEKIT_API_SECRET="your-api-secret"

# Qdrant
QDRANT_URL="your-qdrant-endpoint"
QDRANT_API_KEY="your-qdrant-api-key"

# Model APIs
STT_API_KEY="your-stt-api-key"
LLM_API_KEY="your-llm-api-key"
TTS_API_KEY="your-tts-api-key"
```

---

# 💻 Running the Agent

## Console Mode

Useful for testing routing, tool calling, and LLM responses without audio.

```bash
uv run agent.py console
```

## Development Mode

Runs the complete voice agent connected to your configured LiveKit room.

```bash
uv run agent.py dev
```

---

# ☁️ Deployment

The project includes a production-ready multi-stage Dockerfile.

Deploy the agent to LiveKit Cloud:

```bash
lk agent deploy
```

---

# 📂 Project Structure

```text
Digital_Asset_live_kit/
├── Dockerfile
├── pyproject.toml
├── uv.lock
├── agent.py
└── src/
    ├── config/
    │   └── omnivoice_najdi_tts.py
    │
    ├── plugins/
    │   ├── crm_detector.py
    │   ├── semantic_cache.py
    │   ├── openai_stt_with_temperature.py
    │   ├── openai_tts_with_extra_body.py
    │   ├── custom_f5_tts_plugin.py
    │   ├── nv_tron_stt_plugin.py
    │   └── qwen_tts_plugin.py
    │
    ├── prompts/
    │   └── agent_prompt.py
    │
    └── utils/
        └── text_processing.py
```

---

# 🚀 Technology Stack

| Category | Technologies |
|----------|--------------|
| Voice Infrastructure | LiveKit, WebRTC |
| Speech-to-Text | Cohere Transcribe |
| Large Language Model | Gemma 4 26B |
| Text-to-Speech | Qwen3-TTS, Higgs-Audio-TTS-v3 |
| Vector Database | Qdrant |
| Embeddings | FastEmbed |
| Tool Calling | FastMCP |
| Deployment | Docker, LiveKit Cloud |
| Package Management | uv |

---

# 📄 License

This repository is intended for internal development and deployment. Update this section with your preferred open-source or proprietary license before public release.
