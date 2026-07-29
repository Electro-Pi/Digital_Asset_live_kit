"""The provenance contract for indexed chunks.

Every chunk the assistant can quote must be traceable back to a named, approved
publisher over HTTPS. These are the checks that stop an ingestion bug from
quietly putting uncitable text in front of a patient.
"""

import unittest

from src.ingestion import sources
from src.rag.config import APPROVED_PUBLISHERS
from src.rag.models import ChunkValidationError, GuidelineChunk


def _chunk(**overrides) -> GuidelineChunk:
    base = dict(
        id="uspstf:test:0001",
        publisher="USPSTF",
        guideline_title="USPSTF Recommendation: Colorectal Cancer: Screening",
        section_path="Recommendation Summary",
        text="The USPSTF recommends screening for colorectal cancer in adults 45 to 75.",
        source_url="https://www.uspreventiveservicestaskforce.org/x",
        jurisdiction="US",
        audience="clinician",
    )
    base.update(overrides)
    return GuidelineChunk(**base)  # type: ignore[arg-type]


class ChunkValidationTests(unittest.TestCase):
    def test_valid_chunk_is_accepted(self) -> None:
        self.assertEqual(_chunk().publisher, "USPSTF")

    def test_rejects_unapproved_publisher(self) -> None:
        with self.assertRaises(ChunkValidationError):
            _chunk(publisher="WebMD")

    def test_rejects_non_https_source(self) -> None:
        with self.assertRaises(ChunkValidationError):
            _chunk(source_url="http://www.cdc.gov/x")

    def test_rejects_empty_text(self) -> None:
        with self.assertRaises(ChunkValidationError):
            _chunk(text="   ")

    def test_rejects_unknown_audience(self) -> None:
        with self.assertRaises(ChunkValidationError):
            _chunk(audience="marketing")

    def test_rejects_malformed_date(self) -> None:
        with self.assertRaises(ChunkValidationError):
            _chunk(reviewed_on="May 2021")

    def test_embedding_text_carries_topical_anchoring(self) -> None:
        # A bare paragraph often never names its condition; the header is what
        # keeps the vector on-topic.
        text = _chunk().embedding_text
        self.assertIn("USPSTF", text)
        self.assertIn("Colorectal Cancer", text)
        self.assertIn("Recommendation Summary", text)

    def test_payload_round_trips(self) -> None:
        original = _chunk(topic_tags=("screening_colorectal",), keywords=("crc", "فحص"))
        restored = GuidelineChunk.from_mapping(original.to_payload())
        self.assertEqual(restored, original)


class SourceCatalogTests(unittest.TestCase):
    """The catalogs are the reviewable artifact — they must stay well-formed."""

    def test_all_seven_approved_publishers_are_represented(self) -> None:
        covered = {source.publisher for source in sources.all_sources()}
        self.assertEqual(covered, set(APPROVED_PUBLISHERS))

    def test_every_document_url_is_https(self) -> None:
        for source in sources.all_sources():
            for document in source.documents:
                self.assertTrue(
                    document.url.startswith("https://"),
                    f"{document.doc_id} is not HTTPS",
                )

    def test_document_ids_are_unique(self) -> None:
        seen: set[str] = set()
        for source in sources.all_sources():
            for document in source.documents:
                self.assertNotIn(document.doc_id, seen, f"duplicate {document.doc_id}")
                seen.add(document.doc_id)

    def test_every_source_records_a_licence_note(self) -> None:
        # This text is reproduced verbatim in the corpus report KFSHRC reviews.
        for source in sources.all_sources():
            self.assertTrue(source.license_note.strip(), f"{source.name} has no licence note")

    def test_nice_documents_are_tagged_uk(self) -> None:
        # UK-specific guidance must be flagged so the agent can say so.
        nice = sources.get("nice")
        assert nice is not None
        self.assertEqual(nice.jurisdiction, "UK")


if __name__ == "__main__":
    unittest.main()
