"""Patient utterances must not reach the logs.

Everything a caller says here is health information, and this agent runs on a
shared host with journald collecting everything. The redaction is only worth
having if it is on by default, so that is what these tests pin.
"""

import importlib
import unittest
from unittest import mock


def _agent_with(env: dict):
    """Import agent.py under a given environment.

    The module reads its settings once at import time, so the flag has to be set
    before the import rather than patched afterwards.
    """
    with mock.patch.dict("os.environ", env, clear=False):
        import agent

        return importlib.reload(agent)


class LogRedactionTests(unittest.TestCase):
    def test_transcripts_are_redacted_by_default(self) -> None:
        agent = _agent_with({"LOG_SENSITIVE_CONTENT": "false"})
        utterance = "عندي ألم في الصدر من ثلاثة أيام وعمري خمسين"
        preview = agent._preview(utterance)
        self.assertNotIn("ألم", preview)
        self.assertNotIn("الصدر", preview)
        self.assertIn("redacted", preview)
        # A length is still useful for debugging and reveals nothing clinical.
        self.assertIn(str(len(utterance)), preview)

    def test_opt_in_restores_content_for_local_debugging(self) -> None:
        agent = _agent_with({"LOG_SENSITIVE_CONTENT": "true"})
        self.assertIn("ألم", agent._preview("عندي ألم في الصدر"))

    def test_provider_errors_do_not_echo_request_bodies(self) -> None:
        agent = _agent_with({"LOG_SENSITIVE_CONTENT": "false"})

        class FakeProviderError(Exception):
            status_code = 400

            def __str__(self) -> str:
                # Provider SDKs routinely stringify the whole request.
                return "400 Bad Request: {'messages': [{'content': 'عندي ألم في الصدر'}]}"

        summary = agent._error_summary(FakeProviderError())
        self.assertNotIn("الصدر", summary)
        self.assertIn("status=400", summary)
        self.assertIn("redacted", summary)


class SettingsDefaultTests(unittest.TestCase):
    def test_sensitive_logging_defaults_off(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            from src.rag.config import RAGSettings

            self.assertFalse(RAGSettings().log_sensitive_content)


if __name__ == "__main__":
    unittest.main()
