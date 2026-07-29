"""Configuration for the medical guideline RAG stack.

Every knob is read from the environment once and frozen, so the agent worker,
the ingestion CLI and the tests all agree on the same collection name, vector
size and thresholds. A mismatch between ingest-time and query-time settings is
the classic way to silently break retrieval, so they share one source of truth.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# The seven guideline bodies the KFSHRC PoC approves. Anything outside this set
# must never reach the patient-facing index — the scope document requires the
# assistant to rely exclusively on these publishers.
APPROVED_PUBLISHERS: tuple[str, ...] = (
    "USPSTF",
    "AAFP",
    "NICE",
    "ACC/AHA",
    "CDC",
    "AAP",
    "ACOG",
)

# Spoken Arabic names, used by the prompt so the agent cites a source the way a
# Saudi patient would hear it rather than reading out an English acronym alone.
PUBLISHER_ARABIC_NAMES: dict[str, str] = {
    "USPSTF": "فريق الخدمات الوقائية الأمريكي",
    "AAFP": "الأكاديمية الأمريكية لأطباء الأسرة",
    "NICE": "المعهد الوطني البريطاني للصحة وجودة الرعاية",
    "ACC/AHA": "الكلية الأمريكية لطب القلب وجمعية القلب الأمريكية",
    "CDC": "المراكز الأمريكية لمكافحة الأمراض والوقاية منها",
    "AAP": "الأكاديمية الأمريكية لطب الأطفال",
    "ACOG": "الكلية الأمريكية لأطباء النساء والولادة",
}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class RAGSettings:
    # ─── Vector store ────────────────────────────────────────────────────────
    qdrant_url: str = field(
        default_factory=lambda: os.getenv("QDRANT_URL", "http://localhost:6335")
    )
    qdrant_api_key: str | None = field(
        default_factory=lambda: os.getenv("QDRANT_API_KEY", "").strip() or None
    )
    # Reads and writes go through the alias; ingestion builds a fresh versioned
    # collection and repoints the alias atomically, so a rebuild never leaves
    # the live agent pointing at a half-filled index.
    collection_alias: str = field(
        default_factory=lambda: os.getenv("QDRANT_COLLECTION", "medical_guidelines")
    )
    corpus_version: str = field(
        default_factory=lambda: os.getenv("CORPUS_VERSION", "v1")
    )

    # ─── Embeddings ──────────────────────────────────────────────────────────
    google_api_key: str | None = field(
        default_factory=lambda: os.getenv("GOOGLE_API_KEY", "").strip() or None
    )
    embedding_model: str = field(
        default_factory=lambda: os.getenv(
            "GEMINI_EMBEDDING_MODEL", "gemini-embedding-001"
        )
    )
    # 1536 is a Matryoshka truncation of the model's native 3072. It halves
    # storage and search cost for a negligible quality loss, but truncated
    # vectors are NOT unit-length as returned, so the embedder renormalizes.
    embedding_dim: int = field(default_factory=lambda: _env_int("EMBEDDING_DIM", 1536))
    embedding_batch_size: int = field(
        default_factory=lambda: _env_int("EMBEDDING_BATCH_SIZE", 32)
    )
    embedding_timeout_s: float = field(
        default_factory=lambda: _env_float("EMBEDDING_TIMEOUT_S", 10.0)
    )
    # Seconds of quota budget per embedded text. Gemini's free tier counts 100
    # texts per minute (batching does not exempt them), so 0.65 leaves a little
    # headroom under that. Lower it to near zero on a paid key — at this rate a
    # full corpus build takes roughly twenty minutes.
    embedding_seconds_per_item: float = field(
        default_factory=lambda: _env_float("EMBEDDING_SECONDS_PER_ITEM", 0.65)
    )

    # ─── Retrieval ───────────────────────────────────────────────────────────
    top_k: int = field(default_factory=lambda: _env_int("RAG_TOP_K", 4))
    score_threshold: float = field(
        default_factory=lambda: _env_float("RAG_SCORE_THRESHOLD", 0.55)
    )
    # Ceiling on everything between end-of-utterance and the LLM call. Blowing
    # it drops the evidence rather than the turn: the prompt's "no evidence"
    # rule then makes the agent refer the patient instead of inventing content.
    #
    # Sized well above the measured p95 (~360ms against the Gemini API from the
    # deployment host) rather than close to it. Losing evidence costs a real
    # answer the patient could have had, while the extra latency is invisible
    # next to a TTS stage that already waits for the whole LLM turn — so this
    # is a stall detector, not a tuning knob.
    timeout_ms: int = field(default_factory=lambda: _env_int("RAG_TIMEOUT_MS", 700))
    history_turns: int = field(
        default_factory=lambda: _env_int("RAG_HISTORY_TURNS", 4)
    )
    # Run the classifier and the embed+search concurrently, discarding the
    # search when the label turns out not to need it. Costs a few wasted
    # embedding calls, saves ~200ms of serial latency on every clinical turn.
    speculative: bool = field(
        default_factory=lambda: _env_bool("RAG_SPECULATIVE", True)
    )

    # ─── Classifier ──────────────────────────────────────────────────────────
    llm_base_url: str = field(
        default_factory=lambda: os.getenv("LLM_BASE_URL", "http://localhost:8001/v1")
    )
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "gemma4"))
    llm_api_key: str = field(
        default_factory=lambda: os.getenv("LLM_API_KEY", "").strip() or "no-key-needed"
    )
    classifier_model: str = field(
        default_factory=lambda: os.getenv("CLASSIFIER_MODEL", "").strip()
        or os.getenv("LLM_MODEL", "gemma4")
    )
    classifier_max_tokens: int = field(
        default_factory=lambda: _env_int("CLASSIFIER_MAX_TOKENS", 8)
    )
    classifier_timeout_s: float = field(
        default_factory=lambda: _env_float("CLASSIFIER_TIMEOUT_S", 3.0)
    )

    # ─── Privacy ─────────────────────────────────────────────────────────────
    # Off by default: patient utterances are health information and must not
    # land in logs on a deployed box. Flip only on a dev machine.
    log_sensitive_content: bool = field(
        default_factory=lambda: _env_bool("LOG_SENSITIVE_CONTENT", False)
    )

    @property
    def versioned_collection(self) -> str:
        return f"{self.collection_alias}_{self.corpus_version}"


def settings_from_env() -> RAGSettings:
    """Build settings from the current environment.

    Called at import time by the agent and per-invocation by the CLI, so
    ``load_dotenv`` must already have run.
    """
    return RAGSettings()
