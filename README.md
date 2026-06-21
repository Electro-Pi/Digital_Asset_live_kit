# 🎙️ Sera with MCP (Model Context Protocol)

**Sera (سيرا)** is an enterprise-grade, real-time voice assistant built on LiveKit and integrated with a remote **FastMCP (Model Context Protocol)** server. This repository orchestrates a low-latency WebRTC pipeline that dynamically routes customer interactions between a high-speed semantic cache and a live CRM backend.

---

## ✨ Key Features

* **FastMCP Integration:** Securely connects to a remote MCP server via SSE to execute the `sera_chat` tool, enabling real-time database queries, CRM lookups, and ticket tracking.
* **Intelligent Routing (CRM vs. Knowledge):** Automatically detects user intent. General queries are routed to a Qdrant-backed Semantic Cache, while sensitive/dynamic requests (e.g., National IDs, complaint numbers) bypass the cache and hit the MCP backend directly.
* **Strict Dialect Governance:** Enforces the **Saudi Najdi** dialect dynamically. System prompts ensure all numerical values are converted to Arabic text (e.g., "ثلاثة" instead of "3") and markdown is stripped to prevent TTS engine stuttering.
* **Latency Masking:** Plays contextual background audio (office ambiance and keyboard typing sounds) while fetching data from the FastMCP server, maintaining a natural, human-like conversational flow.

---

## 🏗️ System Architecture

1. **VAD & STT:** Audio is captured via LiveKit, processed by Silero VAD, and transcribed using a self-hosted Cohere Transcribe model (temperature locked at `0.0` for deterministic Arabic parsing).
2. **Intent & Cache Layer:** * The `is_crm_action()` detector flags dynamic requests.
   * General knowledge queries hit the `sera_cache`. On a cache hit, the LLM is instructed to read the answer in Najdi.
3. **MCP Execution:** On a cache miss or CRM action, the agent calls the remote FastMCP server using a unique, per-call `session_id`.
4. **Sanitization:** Backend responses are passed through `clean_backend_text()` to strip symbols, brackets, and markdown.
5. **TTS:** Processed text is sent to an OmniVoice/Qwen TTS engine optimized for Najdi prosody via continuous batching.

---

## ⚙️ Prerequisites

* **Python:** 3.12+
* **Package Manager:** `uv` (recommended for fast dependency resolution)
* **LiveKit Server:** A cloud or self-hosted LiveKit instance
* **Remote Endpoints:** Active vLLM endpoints for your custom STT, LLM, and TTS models.

---

## 🚀 Installation & Setup

**1. Clone the repository**
```bash
git clone [https://github.com/Electro-Pi/sera-with-mcp.git](https://github.com/Electro-Pi/sera-with-mcp.git)
cd sera-with-mcp
