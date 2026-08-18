import os
import re
import threading

# Windows HF cache: symlinks need elevated privileges — copy files instead.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")

from qdrant_client import QdrantClient, models
from loguru import logger

# Legacy default for collections created by this agent.
VECTOR_NAME = "query_vector"
DENSE_VECTOR_NAME = "dense"
SPARSE_VECTOR_NAME = "sparse"

_PAYLOAD_TEXT_KEYS = (
    "response",
    "content",
    "text",
    "answer",
    "chunk",
    "page_content",
    "document",
)

# Chunk metadata labels that should not be spoken aloud.
_METADATA_LABELS = re.compile(
    r"(?:الملخص|العنوان|عنوان القسم الفرعي|عنوان القسم|المصدر)\s*:",
    re.IGNORECASE,
)

# Pipe-delimited chunk structure: "الملخص: ... | العنوان: ... | المحتوى: <actual text>"
_CONTENT_SECTION = re.compile(r"[|]\s*المحتوى\s*:\s*(.+)", re.DOTALL)
_AR_TOKEN_RE = re.compile(r"[ء-ي]{3,}")
_AR_STOPWORDS = {
    "هذا",
    "هذه",
    "ذلك",
    "التي",
    "الذي",
    "على",
    "الى",
    "إلى",
    "من",
    "في",
    "عن",
    "هل",
    "كيف",
    "متى",
    "وين",
    "وش",
    "ايش",
    "انا",
    "أنت",
    "انتي",
    "ممكن",
    "لو",
    "بعد",
    "قبل",
    "عند",
    "كان",
    "يكون",
    "تكون",
    "طلب",
}
_WEAK_MATCH_TERMS = {
    "خدمة",
    "الخدمة",
    "تقديم",
    "الطلب",
    "طلب",
    "رقم",
    "جديدة",
    "المعلومات",
    "الهيئة",
    "يتم",
}
_QUERY_ANCHORS = (
    "رقم",
    "رسوم",
    "حالة",
    "الغي",
    "إلغاء",
    "رفض",
    "رفضوا",
    "تواصل",
    "اتواصل",
    "حرية",
    "المعلومات",
    "شكوى",
    "الشكوى",
)

_INTENT_RULES: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    # Customer support number/contact intent.
    (
        ("رقم", "خدمة العملاء", "مركز الاتصال", "اتواصل", "تواصل"),
        ("رقم", "الهاتف", "اتصال", "مركز", "واتساب", "بريد", "@", "19944"),
    ),
    # Fees/cost intent.
    (
        ("رسوم", "تكلفة", "سعر", "مقابل", "مجاني"),
        ("رسوم", "تكلفة", "مقابل", "ريال", "مجان", "بدون رسوم"),
    ),
    # Request status/follow-up intent.
    (
        ("حالة الطلب", "حالة", "متابعة", "الطلب"),
        ("حالة", "متابعة", "رقم الطلب", "الطلب", "بوابة", "تتبع"),
    ),
    # Rejected complaint / escalation intent.
    (
        ("رفض", "مرفوض", "رفضوا", "تصعيد", "اعتراض"),
        ("تصعيد", "اعتراض", "إعادة", "مراجعة", "قبول", "رفض", "الشكوى"),
    ),
)


def _is_phone_number_query(query: str) -> bool:
    q = query or ""
    if "خدمة العملاء" in q or "مركز الاتصال" in q or "مركز اتصال" in q:
        return True
    if "رقم" in q and any(s in q for s in ("العملاء", "مركز", "اتصال", "هاتف", "تواصل", "اتصل")):
        return True
    return False


def _is_phone_number_query(query: str) -> bool:
    q = query or ""
    if "خدمة العملاء" in q or "مركز الاتصال" in q or "مركز اتصال" in q:
        return True
    if "رقم" in q and any(s in q for s in ("العملاء", "مركز", "اتصال", "هاتف", "تواصل", "اتصل")):
        return True
    return False


def _is_fees_query(query: str) -> bool:
    q = query or ""
    return any(s in q for s in ("رسوم", "تكلفة", "تكاليف", "سعر", "أسعار", "اسعار", "مجاني", "مقابل"))


def _intent_relevance_ok(query: str, response: str) -> bool:
    q = query or ""
    r = response or ""

    # Strict only where wrong answers are very harmful; keep others permissive.
    if _is_phone_number_query(q):
        return (
            ("خدمة العملاء" in r or "مركز الاتصال" in r or "مركز اتصال" in r)
            and ("19944" in r or "800" in r or re.search(r"[0-9٠-٩]{3,}", r) is not None)
        )
    if ("حالة" in q and "طلب" in q) or ("متابعة" in q and "طلب" in q):
        return any(s in r for s in ("حالة الطلب", "رقم الطلب", "متابعة", "تتبع", "الاستعلام", "بوابة"))
    if _is_fees_query(q) or any(s in q for s in ("تعريفة", "تعرفة", "اسعار", "أسعار")):
        return any(s in r for s in ("رسوم", "تكلفة", "تكاليف", "مقابل", "ريال", "مجان", "تعريفة", "تعرفة", "كهرب", "استهلاك", "خدمات", "منشآت"))
    if ("رفض" in q or "رفضوا" in q) and ("شكوى" in q or "الشكوى" in q):
        return any(s in r for s in ("اعتراض", "تصعيد", "رفض", "إعادة", "الشكوى", "شكوى"))
    if ("افتح" in q or "جديدة" in q or "تقديم" in q) and ("شكوى" in q or "الشكوى" in q):
        if "إغلاق" in r or "اغلاق" in r:
            return False
        return any(s in r for s in ("تقديم الشكوى", "تقديم", "فتح", "خدمة تقديم"))
    if ("اتابع" in q or "متابعة" in q or "وين وصلت" in q or "وصلت" in q) and (
        "شكوى" in q or "الشكوى" in q or "شكواي" in q
    ):
        return any(s in r for s in ("متابعة", "رقم الشكوى", "الاستعلام", "حالة", "رسالة"))
    if "تقييم" in q and ("خدمة" in q or "الشكوى" in q):
        return any(s in r for s in ("تقييم", "رضا", "قياس"))

    for query_signals, response_signals in _INTENT_RULES:
        if any(s in q for s in query_signals):
            if "رقم" in query_signals and not _is_phone_number_query(q):
                continue
            return any(s in r for s in response_signals)
    return True


_ANCHOR_RELATED: dict[str, tuple[str, ...]] = {
    "شكوى": ("شكوى", "الشكوى", "الشكاوى", "شكاوى"),
    "الشكوى": ("شكوى", "الشكوى", "الشكاوى", "شكاوى"),
    "حرية": ("حرية", "المعلومات"),
    "المعلومات": ("حرية", "المعلومات", "معلومات"),
}


def _anchor_matches(query: str, response: str) -> bool:
    anchor_hits = [a for a in _QUERY_ANCHORS if a in query]
    if not anchor_hits:
        return True
    for anchor in anchor_hits:
        forms = _ANCHOR_RELATED.get(anchor, (anchor,))
        if any(form in response for form in forms):
            return True
    return False


def _clean_content_body(text: str) -> str:
    """Clean the raw body text for TTS: convert list commas and artifacts to readable prose."""
    # Numbered list items like "1 تقديم الشكوى,2 التحقق" → separate with period
    text = re.sub(r",\s*(\d+)\s+", ". ", text)
    # Leading digit + space like "1 تقديم" at start
    text = re.sub(r"^\s*\d+\s+", "", text)
    # Bare digits followed by Arabic (section numbers "1-1-9")
    text = re.sub(r"\b\d+[-\d]*\b\s*", "", text)
    # English letters (e.g. "a-" "b-" "ج-" already handled elsewhere)
    text = re.sub(r"\b[a-zA-Z]\b[-.]?\s*", "", text)
    # Repeated commas/newlines → sentence break
    text = re.sub(r",{2,}|،{2,}", "،", text)
    text = re.sub(r"[\n\r]+", "، ", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def _extract_spoken_text(raw: str) -> str:
    """Extract only the speakable portion from a Qdrant chunk.

    Chunks look like:
      الملخص: ... | العنوان: ... | عنوان القسم: ... | المحتوى: <actual text>

    We want just the المحتوى section if present; otherwise use الملخص (summary)
    as a fallback since it is already a readable sentence.
    """
    # 1. Extract الملخص (summary) — always a clean readable sentence.
    summary = ""
    summary_m = re.match(r"الملخص\s*:\s*(.+?)(?:\s*[|]|$)", raw)
    if summary_m:
        summary = summary_m.group(1).strip()

    # 2. Try المحتوى section.
    m = _CONTENT_SECTION.search(raw)
    if m:
        body = m.group(1).strip()
        body = re.split(r"\s*[|]\s*(?:الملخص|العنوان|عنوان)", body)[0].strip()
        body = _clean_content_body(body)
        # If المحتوى is rich enough (not just a numbered list header), return it.
        # Prepend the summary so the answer opens with a natural sentence.
        if len(body) >= 60:
            if summary and not body.startswith(summary[:30]):
                return f"{summary}. {body}"
            return body

    # 3. الملخص alone as fallback.
    if len(summary) >= 20:
        return summary

    # 3. Last resort — strip all metadata label segments.
    parts = [p.strip() for p in raw.split("|")]
    spoken_parts = []
    for part in parts:
        if _METADATA_LABELS.match(part):
            after_colon = part.split(":", 1)[-1].strip()
            if len(after_colon) >= 30 and not _METADATA_LABELS.match(after_colon):
                spoken_parts.append(after_colon)
        elif part:
            spoken_parts.append(part)

    result = _clean_content_body(" ".join(spoken_parts))
    return result if result else raw


def _key_terms(text: str) -> set[str]:
    terms = {t for t in _AR_TOKEN_RE.findall(text or "") if t not in _AR_STOPWORDS}
    return terms


def _is_relevant_hit(query: str, response: str) -> bool:
    """Guard against high-score but semantically unrelated BM25 hits."""
    q_terms = _key_terms(query)
    if not q_terms:
        return True
    r_terms = _key_terms(response)
    if not r_terms:
        return False
    overlap = q_terms & r_terms
    if not overlap:
        return False

    if not _intent_relevance_ok(query, response):
        return False

    if not _anchor_matches(query, response):
        return False

    strong_overlap = {t for t in overlap if t not in _WEAK_MATCH_TERMS}
    # At least one non-generic shared term, or two generic+specific overlaps.
    if strong_overlap:
        return True
    return len(overlap) >= 2


def _env(key: str, default: str | None = None) -> str | None:
    value = os.getenv(key, default)
    if value is None:
        return None
    return str(value).strip().strip('"').strip("'")


def _env_float(key: str, default: float) -> float:
    raw = _env(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning(f"Invalid float for {key}={raw!r}; using {default}")
        return default


class SemanticCache:
    """Read-only Qdrant RAG for SERA knowledge base."""

    def __init__(self, threshold: float | None = None):
        self.collection_name = _env("QDRANT_COLLECTION", "sera_semantic_cache") or "sera_semantic_cache"
        self.enabled = True

        self.search_mode = (_env("QDRANT_SEARCH_MODE", "auto") or "auto").lower()
        self.sparse_model = _env("QDRANT_SPARSE_MODEL", "Qdrant/bm25") or "Qdrant/bm25"
        self.sparse_language = _env("QDRANT_SPARSE_LANGUAGE", "arabic") or "arabic"
        self.sparse_disable_stemmer = _env("QDRANT_SPARSE_DISABLE_STEMMER", "true").lower() in {
            "1",
            "true",
            "yes",
        }

        self.dense_embedder = None
        self.dense_model_name: str | None = None
        self.sparse_embedder = None
        self._embedder_lock = threading.Lock()

        self.dense_vector_name: str | None = DENSE_VECTOR_NAME
        self.sparse_vector_name: str | None = SPARSE_VECTOR_NAME
        self.dense_vector_size: int | None = None
        self.has_sparse_index = False

        # BM25 scores are much larger than cosine similarity.
        self.sparse_threshold = threshold if threshold is not None else _env_float("QDRANT_RAG_THRESHOLD", 4.0)
        self.dense_threshold = _env_float("QDRANT_DENSE_THRESHOLD", 0.45)

        qdrant_url = _env("QDRANT_URL")
        qdrant_api_key = _env("QDRANT_API_KEY")
        qdrant_host = _env("QDRANT_HOST")
        qdrant_port = _env("QDRANT_PORT")

        try:
            if qdrant_url and qdrant_api_key:
                self.client = QdrantClient(
                    url=qdrant_url,
                    api_key=qdrant_api_key,
                    check_compatibility=False,
                )
                logger.info("Connected to Qdrant Cloud successfully.")
            elif qdrant_host and qdrant_port:
                self.client = QdrantClient(host=qdrant_host, port=int(qdrant_port))
                logger.info(f"Connected to local Qdrant at {qdrant_host}:{qdrant_port}.")
            else:
                logger.warning("No Qdrant credentials found! Falling back to In-Memory DB.")
                self.client = QdrantClient(":memory:")

            self._inspect_collection()
        except Exception as e:
            logger.error(
                f"Qdrant unavailable ({e}). Falling back to in-memory cache so the agent can start."
            )
            self.client = QdrantClient(":memory:")
            try:
                self._inspect_collection()
            except Exception as inner:
                logger.error(f"In-memory Qdrant also failed ({inner}). Cache disabled.")
                self.enabled = False

    def _inspect_collection(self) -> None:
        """Read-only: never delete or recreate an existing Qdrant collection."""
        if not self.client.collection_exists(self.collection_name):
            logger.warning(
                f"Collection '{self.collection_name}' does not exist. "
                "RAG will miss until the collection is populated."
            )
            return

        try:
            info = self.client.get_collection(self.collection_name)
            vectors_cfg = info.config.params.vectors
            if isinstance(vectors_cfg, dict) and vectors_cfg:
                self.dense_vector_name, params = next(iter(vectors_cfg.items()))
                self.dense_vector_size = params.size
            else:
                self.dense_vector_name = None
                self.dense_vector_size = getattr(vectors_cfg, "size", None)

            sparse_cfg = getattr(info.config.params, "sparse_vectors", None) or {}
            self.has_sparse_index = bool(sparse_cfg)
            if isinstance(sparse_cfg, dict) and sparse_cfg:
                self.sparse_vector_name = next(iter(sparse_cfg.keys()))

            effective_mode = self._effective_search_mode()
            logger.info(
                f"Collection '{self.collection_name}' ready "
                f"(dense='{self.dense_vector_name}', size={self.dense_vector_size}, "
                f"sparse='{self.sparse_vector_name if self.has_sparse_index else None}', "
                f"points={info.points_count}, search_mode={effective_mode})."
            )
        except Exception as e:
            logger.warning(f"Could not inspect collection config ({e}). Continuing read-only.")

    def _effective_search_mode(self) -> str:
        if self.search_mode in {"sparse", "dense", "hybrid"}:
            return self.search_mode
        if self.has_sparse_index:
            return "sparse"
        return "dense"

    def _ensure_sparse_embedder(self) -> None:
        if self.sparse_embedder is not None:
            return
        with self._embedder_lock:
            if self.sparse_embedder is not None:
                return
            from fastembed.sparse.bm25 import Bm25

            logger.info(
                f"Loading sparse embedder {self.sparse_model} "
                f"(language={self.sparse_language}, stemmer={not self.sparse_disable_stemmer})..."
            )
            self.sparse_embedder = Bm25(
                model_name=self.sparse_model,
                language=self.sparse_language,
                disable_stemmer=self.sparse_disable_stemmer,
                local_files_only=False,
            )
            logger.info("Sparse embedder ready.")

    def _ensure_dense_embedder(self) -> None:
        if self.dense_embedder is not None:
            return
        with self._embedder_lock:
            if self.dense_embedder is not None:
                return
            from fastembed import TextEmbedding

            model = _env("QDRANT_EMBED_MODEL")
            if not model:
                if self.dense_vector_size == 768:
                    model = "nomic-ai/nomic-embed-text-v1.5"
                else:
                    model = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
            logger.info(f"Loading dense embedder {model}...")
            self.dense_embedder = TextEmbedding(model)
            self.dense_model_name = model
            logger.info("Dense embedder ready.")

    def _embed_dense(self, text: str) -> list[float]:
        self._ensure_dense_embedder()
        query = self.clean_query(text)
        model_name = str(self.dense_model_name or "")
        if "e5" in model_name.lower() and not query.lower().startswith("query:"):
            query = f"query: {query}"
        if "nomic" in model_name.lower() and not query.lower().startswith("search_query:"):
            query = f"search_query: {query}"
        return next(self.dense_embedder.embed([query])).tolist()

    def _embed_sparse(self, text: str) -> models.SparseVector:
        self._ensure_sparse_embedder()
        query = self.clean_query(text)
        sparse = next(self.sparse_embedder.query_embed(query))
        return models.SparseVector(
            indices=sparse.indices.tolist(),
            values=sparse.values.tolist(),
        )

    def clean_query(self, query: str) -> str:
        return re.sub(r"\s+", " ", query).strip()

    def _query_variants(self, query: str) -> list[str]:
        """Dialect/synonym expansions so paraphrases hit the same KB chunk."""
        base = self.clean_query(query)
        if not base:
            return []

        variants: list[str] = [base]
        expanded = base
        replacements = (
            ("كيفية ", "كيف "),
            ("ازاي ", "كيف "),
            ("إزاي ", "كيف "),
            ("عاوز ", "كيف "),
            ("ابغى اعرف ", "كيف "),
            ("ابي اعرف ", "كيف "),
            ("اسعار ", "تعريفة "),
            ("أسعار ", "تعريفة "),
            ("منازل", "سكنية"),
            ("شكواي", "الشكوى"),
            ("وين وصلت", "حالة"),
            ("وصلت شكواي", "حالة الشكوى"),
        )
        for src, dst in replacements:
            if src in expanded:
                expanded = expanded.replace(src, dst)

        if expanded != base:
            variants.append(expanded)

        bare = expanded.rstrip("?.!")
        if "شكو" in bare and "للهيئة" not in bare and "الهيئة" not in bare:
            variants.append(f"{bare} للهيئة؟")

        if not bare.endswith("؟"):
            variants.append(f"{bare}؟")

        # De-duplicate while preserving order.
        seen: set[str] = set()
        ordered: list[str] = []
        for item in variants:
            if item and item not in seen:
                seen.add(item)
                ordered.append(item)
        return ordered

    def _payload_text(self, payload: dict | None) -> str | None:
        if not payload:
            return None
        for key in _PAYLOAD_TEXT_KEYS:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return _extract_spoken_text(value.strip())
        return None

    def is_usable_response(self, response: str | None) -> bool:
        """Reject empty, fallback, or tiny snippets."""
        if not response or not response.strip():
            return False
        if len(response.strip()) < 20:
            return False

        lowered = response.strip().lower()
        blocked_phrases = (
            "ما فهمت وش تقصد",
            "ممكن توضح سؤالك",
            "ما لقيت هالمعلومة",
            "عذراً، ما لقيت",
            "error",
            "failed",
            "cannot find",
            "not found",
        )
        return not any(phrase in lowered for phrase in blocked_phrases)

    def _query_sparse(self, query: str, limit: int = 3) -> list:
        vector = self._embed_sparse(query)
        return self.client.query_points(
            collection_name=self.collection_name,
            query=vector,
            using=self.sparse_vector_name or SPARSE_VECTOR_NAME,
            limit=limit,
            with_payload=True,
        ).points

    def _query_dense(self, query: str, limit: int = 3) -> list:
        vector = self._embed_dense(query)
        query_kwargs = {
            "collection_name": self.collection_name,
            "query": vector,
            "limit": limit,
            "with_payload": True,
        }
        if self.dense_vector_name:
            query_kwargs["using"] = self.dense_vector_name
        return self.client.query_points(**query_kwargs).points

    def _pick_best(self, query: str, points: list, mode: str) -> str | None:
        if not points:
            logger.info(f"RAG MISS | mode={mode} | query='{query[:60]}'")
            return None

        threshold = self.sparse_threshold if mode == "sparse" else self.dense_threshold
        for rank, point in enumerate(points[:3], start=1):
            response = self._payload_text(point.payload)
            logger.info(
                f"RAG cand#{rank} | mode={mode} | score={point.score:.3f} | "
                f"len={len(response or '')} | query='{query[:60]}'"
            )
            if point.score < threshold:
                continue
            if not self.is_usable_response(response):
                continue
            if not _is_relevant_hit(query, response):
                logger.warning(
                    f"RAG cand#{rank} rejected (low relevance) | mode={mode} | "
                    f"score={point.score:.3f} | query='{query[:60]}'"
                )
                continue

            logger.info(
                f"RAG HIT | mode={mode} | score={point.score:.3f} | "
                f"len={len(response)} | query='{query[:60]}'"
            )
            return response

        logger.info(f"RAG MISS after filtering | mode={mode} | query='{query[:60]}'")
        return None

    def get_cached_response(self, query: str) -> str | None:
        if not self.enabled:
            return None

        for variant in self._query_variants(query):
            hit = self._get_cached_response_once(variant)
            if hit:
                if variant != self.clean_query(query):
                    logger.info(f"RAG HIT via variant '{variant[:60]}' for '{query[:60]}'")
                return hit
        return None

    def _get_cached_response_once(self, query: str) -> str | None:
        cleaned = self.clean_query(query)
        if not cleaned:
            return None

        mode = self._effective_search_mode()
        try:
            if mode == "sparse":
                return self._pick_best(cleaned, self._query_sparse(cleaned), "sparse")
            if mode == "dense":
                return self._pick_best(cleaned, self._query_dense(cleaned), "dense")

            sparse_points = self._query_sparse(cleaned)
            sparse_hit = self._pick_best(cleaned, sparse_points[:1], "sparse")
            if sparse_hit:
                return sparse_hit
            return self._pick_best(cleaned, self._query_dense(cleaned), "dense")

        except Exception as e:
            logger.error(f"Qdrant read error: {e}")
            return None

    def add_to_cache(self, query: str, response: str):
        """Disabled — Qdrant is read-only RAG; no writes."""
        return


sera_cache = SemanticCache()
