"""Week 11 project tests. Mocked network is not a live LLM API call."""
import io
import json
import os
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from src.openrouter_client import ENDPOINT, LLMError, OpenRouterClient
from src.tender_classifier import analyze_notice, validate_classification


def mock_completion(content):
    return io.BytesIO(json.dumps({
        "id": "mock-completion-123",
        "model": "mock/free",
        "choices": [{"message": {"content": content}}],
        "usage": {"total_tokens": 40},
    }).encode("utf-8"))


class FakeClient:
    def __init__(self, content):
        self.content = content
        self.messages = None

    def complete(self, messages):
        self.messages = messages
        return {
            "text": self.content, "model": "mock/free",
            "request_id": "mock-completion-123",
            "usage": {"total_tokens": 40},
        }


class OpenRouterTests(unittest.TestCase):
    def setUp(self):
        self.client = OpenRouterClient(api_key="synthetic-test-key", model="mock/free")

    def test_post_request_and_model_response(self):
        with patch("src.openrouter_client.urlopen") as opener:
            opener.return_value.__enter__.return_value = mock_completion(
                '{"relevant":true,"project_type":"construction","reason":"New power line"}'
            )
            output = self.client.complete([{"role": "user", "content": "hello"}])
        self.assertEqual(output["model"], "mock/free")
        self.assertEqual(output["request_id"], "mock-completion-123")
        request = opener.call_args.args[0]
        self.assertEqual(request.full_url, ENDPOINT)
        self.assertEqual(request.get_header("Authorization"), "Bearer synthetic-test-key")
        self.assertEqual(json.loads(request.data)["model"], "mock/free")

    def test_missing_key_raises_without_network_request(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": ""}):
            with self.assertRaisesRegex(LLMError, "missing"):
                OpenRouterClient()

    def test_invalid_message_rejected(self):
        with self.assertRaises(ValueError):
            self.client.complete([{"role": "unknown", "content": "hi"}])

    def test_rate_limit_error(self):
        with patch("src.openrouter_client.urlopen", side_effect=HTTPError(
            ENDPOINT, 429, "Rate limited", {}, None
        )):
            with self.assertRaisesRegex(LLMError, "429"):
                self.client.complete([{"role": "user", "content": "hi"}])

    def test_network_error(self):
        with patch("src.openrouter_client.urlopen", side_effect=URLError("offline")):
            with self.assertRaisesRegex(LLMError, "connection failed"):
                self.client.complete([{"role": "user", "content": "hi"}])

    def test_broken_json_response(self):
        with patch("src.openrouter_client.urlopen") as opener:
            opener.return_value.__enter__.return_value = io.BytesIO(b'{"choices":[]}')
            with self.assertRaisesRegex(LLMError, "invalid completion"):
                self.client.complete([{"role": "user", "content": "hi"}])

    def test_tool_call_is_parsed_not_executed(self):
        response = {
            "choices": [{"message": {
                "content": None,
                "tool_calls": [{"id": "call1", "function": {
                    "name": "inspect_tender", "arguments": '{"tender_id":"demo"}'
                }}],
            }}]
        }
        with patch("src.openrouter_client.urlopen") as opener:
            opener.return_value.__enter__.return_value = io.BytesIO(
                json.dumps(response).encode()
            )
            result = self.client.complete(
                [{"role": "user", "content": "research"}],
                tools=[{"type": "function", "function": {"name": "inspect_tender"}}],
            )
        self.assertEqual(result["toolCalls"][0]["name"], "inspect_tender")

    def test_classification_positive(self):
        client = FakeClient('{"relevant":true,"project_type":"reconstruction","reason":"Grid reconstruction"}')
        result = analyze_notice({"title": "Reconstruction of electricity line"}, client)
        self.assertTrue(result["classification"]["relevant"])
        self.assertFalse(result["source_verified"])
        self.assertIn("Classify", client.messages[1]["content"])

    def test_code_fenced_json(self):
        client = FakeClient('```json\n{"relevant":false,"project_type":"uncertain","reason":"No details"}\n```')
        result = analyze_notice({"title": "Unknown construction"}, client)
        self.assertEqual(result["classification"]["project_type"], "uncertain")

    def test_malformed_llm_response_rejected(self):
        with self.assertRaisesRegex(ValueError, "model did not return JSON"):
            analyze_notice({"title": "Any tender"}, FakeClient("not json"))

    def test_conflicting_classification_rejected(self):
        with self.assertRaisesRegex(ValueError, "contradict"):
            validate_classification({
                "relevant": True, "project_type": "not_power_line", "reason": "bad"
            })

    def test_missing_title_rejected(self):
        with self.assertRaisesRegex(ValueError, "title"):
            analyze_notice({"title": ""}, FakeClient("{}"))


if __name__ == "__main__":
    unittest.main()
