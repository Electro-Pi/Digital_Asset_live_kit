"""Stress-test SERA RAG through the same path as the LiveKit agent.

Discovers questions with direct RAG answers, then tests each with multiple
creative paraphrases via Assistant._resolve_rag_message (cache + guard + humanize).
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env.local", override=True)

from src.plugins.crm_detector import is_knowledge_query  # noqa: E402
from src.plugins.semantic_cache import sera_cache  # noqa: E402
from src.utils.speech_format import format_for_speech, softly_shorten_response, finalize_prosody  # noqa: E402

# Import agent helpers after env is loaded.
from agent import (  # noqa: E402
    Assistant,
    _humanize_rag,
    _intent_guard_ok,
    _is_rag_miss_reply,
    _rag_miss_reply,
    _strip_chatty_edges,
)

_JUNK_DISCOVERY_PATTERNS = (
    "نهاية الوثيقة",
    "عنوان الملحق",
    "جدول التوقيعات",
    "قائمة بمحتويات",
    "محتويات الدليل",
    "هذا الجزء",
    "يستكمل هذا الجزء",
)


def _is_usable_discovered_question(text: str) -> bool:
    if len(text) < 20 or len(text) > 100:
        return False
    if any(p in text for p in _JUNK_DISCOVERY_PATTERNS):
        return False
    return True


_SUMMARY_RE = re.compile(r"الملخص\s*:\s*(.+?)(?:\s*[|]|$)")
_TITLE_RE = re.compile(r"العنوان\s*:\s*(.+?)(?:\s*[|]|$)")

# Known-good canonical questions (verified HITs) + creative paraphrase templates.
_CANONICAL_QUESTIONS: list[tuple[str, list[str]]] = [
    (
        "ايه الخدمات اللي تقدمها الهيئة؟",
        [
            "وش الخدمات اللي تقدمونها؟",
            "عرفيني خدمات الهيئة",
            "ايش تقدرون تساعدوني فيه؟",
            "الخدمات المتاحة من الهيئة السعودية لتنظيم الكهرباء",
        ],
    ),
    (
        "كيف أقدم شكوى للهيئة؟",
        [
            "كيفية تقديم شكوى",
            "ازاي اقدم شكوى كهرباء؟",
            "وش خطوات رفع شكوى للهيئة؟",
            "ابغى اعرف كيف اقدم شكوى",
        ],
    ),
    (
        "كيف أتصعيد الشكوى للهيئة؟",
        [
            "متى أصعّد الشكوى للهيئة؟",
            "وش شروط تصعيد الشكوى؟",
            "كيف ارفع الشكوى لمقدم الخدمة اولاً؟",
            "ليش لازم اتواصل مع مقدم الخدمة قبل الهيئة؟",
        ],
    ),
    (
        "كيف أتابع حالة الشكوى؟",
        [
            "وين اشوف حالة شكواي؟",
            "هل توصل رسالة بحالة الشكوى؟",
            "كيف اعرف وين وصلت شكواي؟",
        ],
    ),
    (
        "ما هي التعريفة الكهربائية للوحدات السكنية؟",
        [
            "كم سعر الكهرباء للبيوت؟",
            "تعريفة الاستهلاك السكني",
            "اسعار الكهرباء للمنازل",
        ],
    ),
    (
        "ما هي حرية المعلومات في الهيئة؟",
        [
            "كيف اطلب حرية معلومات؟",
            "وش طريقة تقديم طلب حرية المعلومات؟",
            "ازاي اقدم طلب معلومات للهيئة؟",
        ],
    ),
    (
        "ما هي الطاقة الشمسية الصغيرة؟",
        [
            "كيف اربط نظام شمسي صغير؟",
            "شروط الربط للطاقة الشمسية الكهروضوئية",
            "متطلبات النظام الشمسي الصغير",
        ],
    ),
    (
        "ما حقوق المستهلك في قطاع الكهرباء؟",
        [
            "وش حقوقي كمستهلك كهرباء؟",
            "حقوق المستهلك الكهربائي",
            "ايش حقوقي تجاه مقدم الخدمة؟",
        ],
    ),
    (
        "كيف أقدم طلب حرية المعلومات؟",
        [
            "طريقة تقديم طلب حرية المعلومات",
            "وين اقدم طلب حرية المعلومات؟",
            "هل اقدر اقدم طلب حرية معلومات ورقي؟",
        ],
    ),
    (
        "ما إجراءات معالجة الشكوى لدى الهيئة؟",
        [
            "مراحل معالجة الشكوى",
            "وش يصير بعد ما اقدم الشكوى؟",
            "خطوات معالجة الشكوى في الهيئة",
        ],
    ),
]

_CHATTY_PREFIXES = (
    "أوضح لك",
    "اوضح لك",
    "طبعًا",
    "طبعاً",
    "هلا",
    "حياك",
)


@dataclass
class TestCase:
    canonical: str
    query: str
    expected_hit: bool = True


@dataclass
class TestResult:
    query: str
    canonical: str
    ok: bool
    issues: list[str] = field(default_factory=list)
    raw_len: int = 0
    spoken_len: int = 0
    preview: str = ""


async def resolve_like_agent(message: str) -> tuple[str | None, str | None, list[str]]:
    """Mirror Assistant._resolve_rag_message without LiveKit session."""
    issues: list[str] = []
    rag_answer = sera_cache.get_cached_response(message)
    if not rag_answer:
        return None, _rag_miss_reply(message), ["rag_miss"]

    cleaned = format_for_speech(rag_answer)
    if not cleaned:
        return rag_answer, _rag_miss_reply(message), ["empty_after_format"]

    if not _intent_guard_ok(message, cleaned):
        return rag_answer, _rag_miss_reply(message), ["intent_guard_reject"]

    if len(cleaned) <= 150:
        spoken = softly_shorten_response(
            finalize_prosody(cleaned),
            max_chars=max(120, len(cleaned)),
            max_sentences=4,
        )
        return rag_answer, spoken, issues

    humanized = await _humanize_rag(cleaned)
    humanized = _strip_chatty_edges(humanized)
    if len(humanized) < max(60, int(len(cleaned) * 0.45)):
        humanized = cleaned

    target_chars = max(140, int(len(cleaned) * 0.82))
    spoken = softly_shorten_response(
        finalize_prosody(humanized),
        max_chars=target_chars,
        max_sentences=4,
    )
    if len(spoken) >= len(cleaned):
        spoken = softly_shorten_response(
            cleaned,
            max_chars=max(120, int(len(cleaned) * 0.9)),
            max_sentences=4,
        )

    return rag_answer, spoken, issues


def _discover_from_qdrant(limit: int = 200) -> list[str]:
    """Extract candidate questions from Qdrant summaries."""
    if not sera_cache.enabled or not sera_cache.client.collection_exists(sera_cache.collection_name):
        return []

    scrolled, _ = sera_cache.client.scroll(
        collection_name=sera_cache.collection_name,
        limit=limit,
        with_payload=True,
    )
    questions: list[str] = []
    seen: set[str] = set()
    for point in scrolled:
        payload = point.payload or {}
        raw = payload.get("content") or payload.get("text") or ""
        if not isinstance(raw, str):
            continue
        summary_m = _SUMMARY_RE.search(raw)
        title_m = _TITLE_RE.search(raw)
        for m in (summary_m, title_m):
            if not m:
                continue
            text = m.group(1).strip().rstrip(".")
            if len(text) < 15 or text in seen:
                continue
            if not _is_usable_discovered_question(text):
                continue
            seen.add(text)
            # Turn summary into a question-like query.
            if "؟" in text or "?" in text:
                q = text
            elif text.startswith("يوضح") or text.startswith("يشرح"):
                q = "ما " + text.replace("يوضح ", "").replace("يشرح ", "") + "؟"
            elif text.startswith("طرق"):
                q = text + "؟"
            else:
                q = f"ما هو {text}؟"
            questions.append(q[:120])
    return questions


def build_test_cases() -> list[TestCase]:
    cases: list[TestCase] = []
    for canonical, paraphrases in _CANONICAL_QUESTIONS:
        cases.append(TestCase(canonical=canonical, query=canonical))
        for p in paraphrases:
            cases.append(TestCase(canonical=canonical, query=p))

    # Add discovered questions that already HIT on canonical form.
    for q in _discover_from_qdrant():
        if sera_cache.get_cached_response(q):
            cases.append(TestCase(canonical=q, query=q))
            cases.append(TestCase(canonical=q, query=q.replace("ما هو ", "وش ").replace("؟", "؟")))
            cases.append(TestCase(canonical=q, query=f"ابغى اعرف {q.lstrip('ما هو ').rstrip('؟')}"))

    # Deduplicate by (canonical, query)
    seen: set[tuple[str, str]] = set()
    unique: list[TestCase] = []
    for c in cases:
        key = (c.canonical, c.query)
        if key in seen:
            continue
        seen.add(key)
        if not is_knowledge_query(c.query):
            continue
        unique.append(c)
    return unique


def validate_result(
    case: TestCase,
    raw: str | None,
    spoken: str | None,
    pipeline_issues: list[str],
) -> TestResult:
    issues = list(pipeline_issues)
    if case.expected_hit:
        if raw is None:
            issues.append("expected_hit_but_rag_miss")
        if spoken is None or _is_rag_miss_reply(spoken):
            issues.append("spoken_is_miss_reply")
        if spoken and not spoken.strip().endswith("."):
            issues.append("no_terminal_punctuation")
        if spoken and any(spoken.startswith(p) for p in _CHATTY_PREFIXES):
            issues.append("chatty_opener")
        if raw and spoken:
            raw_clean = format_for_speech(raw)
            if len(spoken) > len(raw_clean):
                issues.append("spoken_longer_than_raw")
            if len(spoken) < min(40, max(20, int(len(format_for_speech(raw)) * 0.35))):
                issues.append("spoken_too_short")
            # Completeness: at least one substantive word from raw appears in spoken.
            raw_words = {w for w in re.findall(r"[ء-ي]{4,}", raw_clean) if len(w) >= 4}
            spoken_words = set(re.findall(r"[ء-ي]{4,}", spoken))
            if raw_words and len(raw_words & spoken_words) < 1:
                issues.append("no_content_overlap")

    return TestResult(
        query=case.query,
        canonical=case.canonical,
        ok=not issues,
        issues=issues,
        raw_len=len(raw or ""),
        spoken_len=len(spoken or ""),
        preview=(spoken or "")[:100],
    )


async def run_stress_test(humanize: bool = True) -> dict:
    # Filter to questions with direct RAG answers only.
    direct: list[tuple[str, list[str]]] = []
    for canonical, paraphrases in _CANONICAL_QUESTIONS:
        if sera_cache.get_cached_response(canonical):
            direct.append((canonical, paraphrases))
        else:
            print(f"SKIP (no direct answer): {canonical}")

    cases: list[TestCase] = []
    for canonical, paraphrases in direct:
        cases.append(TestCase(canonical=canonical, query=canonical))
        for p in paraphrases:
            cases.append(TestCase(canonical=canonical, query=p))

    # Extra discovered hits.
    for q in _discover_from_qdrant():
        if sera_cache.get_cached_response(q) and not any(q == c[0] for c in direct):
            cases.append(TestCase(canonical=q, query=q))
            alt = q.replace("ما هو ", "ايش ").replace("ما ", "وش ")
            if alt != q:
                cases.append(TestCase(canonical=q, query=alt))

    results: list[TestResult] = []
    t0 = time.time()
    for i, case in enumerate(cases, 1):
        if not humanize:
            # Fast path: cache + guard only.
            raw = sera_cache.get_cached_response(case.query)
            spoken = None
            issues: list[str] = []
            if not raw:
                spoken = _rag_miss_reply(case.query)
                issues.append("rag_miss")
            elif not _intent_guard_ok(case.query, format_for_speech(raw)):
                spoken = _rag_miss_reply(case.query)
                issues.append("intent_guard_reject")
            else:
                spoken = format_for_speech(raw)
        else:
            raw, spoken, issues = await resolve_like_agent(case.query)

        result = validate_result(case, raw, spoken, issues)
        results.append(result)
        status = "OK" if result.ok else "FAIL"
        print(f"[{i}/{len(cases)}] {status} | {case.query[:55]}")
        if not result.ok:
            print(f"       issues={result.issues} | preview={result.preview}")

    elapsed = time.time() - t0
    failed = [r for r in results if not r.ok]
    summary = {
        "total": len(results),
        "passed": len(results) - len(failed),
        "failed": len(failed),
        "elapsed_s": round(elapsed, 1),
        "failures": [
            {"query": r.query, "canonical": r.canonical, "issues": r.issues}
            for r in failed
        ],
    }
    return summary


async def main() -> int:
    print("=== SERA RAG Stress Test (agent path) ===\n")
    summary = await run_stress_test(humanize=True)
    print("\n=== SUMMARY ===")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    out = ROOT / "scripts" / "rag_stress_results.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nResults saved to {out}")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
