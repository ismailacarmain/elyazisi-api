import json
import unittest
from unittest.mock import MagicMock, patch

import ai_provider


class ProviderFallbackTests(unittest.TestCase):
    def setUp(self):
        self.schema = {
            "type": "OBJECT",
            "properties": {"status": {"type": "STRING"}},
            "required": ["status"],
        }
        self.messages = [{"role": "user", "content": "JSON döndür"}]

    @staticmethod
    def response(payload, *, ok=True, status=200):
        result = MagicMock()
        result.ok = ok
        result.status_code = status
        result.json.return_value = payload
        return result

    def test_configured_provider_names_never_include_keys(self):
        providers = ai_provider.configured_providers({
            "gemini_key": "AIza" + "x" * 32,
            "groq_key": "gsk_" + "y" * 32,
            "openrouter_key": "",
        })
        self.assertEqual(["gemini", "groq"], providers)

    def test_gemini_quota_falls_back_to_groq_structured_json(self):
        success = self.response({
            "choices": [{"message": {"content": json.dumps({"status": "OK"})}}]
        })
        error = RuntimeError("quota")
        error.status_code = 429
        with patch("ai_provider.requests.post", return_value=success) as post:
            parsed, provider, model = ai_provider.call_structured_with_fallback(
                config={
                    "gemini_key": "AIza" + "x" * 32,
                    "gemini_model": "gemini-3.5-flash",
                    "groq_key": "gsk_" + "y" * 32,
                    "groq_model": "openai/gpt-oss-120b",
                },
                gemini_call=lambda _key: (_ for _ in ()).throw(error),
                messages=self.messages,
                schema=self.schema,
                schema_name="test_schema",
                max_tokens=128,
            )
        self.assertEqual({"status": "OK"}, parsed)
        self.assertEqual("groq", provider)
        self.assertEqual("openai/gpt-oss-120b", model)
        url = post.call_args.args[0]
        kwargs = post.call_args.kwargs
        self.assertEqual("https://api.groq.com/openai/v1/chat/completions", url)
        self.assertNotIn("gsk_", url)
        self.assertTrue(kwargs["headers"]["Authorization"].startswith("Bearer gsk_"))
        response_schema = kwargs["json"]["response_format"]["json_schema"]["schema"]
        self.assertEqual("object", response_schema["type"])
        self.assertEqual("string", response_schema["properties"]["status"]["type"])

    def test_groq_failure_falls_back_to_openrouter(self):
        denied = self.response({"error": {"message": "limited"}}, ok=False, status=429)
        success = self.response({
            "choices": [{"message": {"content": '{"status":"OK"}'}}]
        })
        with patch("ai_provider.requests.post", side_effect=[denied, success]) as post:
            parsed, provider, model = ai_provider.call_structured_with_fallback(
                config={
                    "groq_key": "gsk_" + "y" * 32,
                    "openrouter_key": "sk-or-v1-" + "z" * 32,
                },
                gemini_call=lambda _key: {},
                messages=self.messages,
                schema=self.schema,
                schema_name="test_schema",
                max_tokens=128,
            )
        self.assertEqual("OK", parsed["status"])
        self.assertEqual("openrouter", provider)
        self.assertEqual("openrouter/free", model)
        self.assertEqual(2, post.call_count)
        payload = post.call_args.kwargs["json"]
        self.assertEqual("json_schema", payload["response_format"]["type"])
        self.assertEqual(
            "object",
            payload["response_format"]["json_schema"]["schema"]["type"],
        )
        self.assertEqual({"require_parameters": True}, payload["provider"])

    def test_openai_uses_structured_chat_completion_without_sampling_controls(self):
        success = self.response({
            "choices": [{"message": {"content": '{"status":"OK"}'}}]
        })
        with patch("ai_provider.requests.post", return_value=success) as post:
            parsed, provider, model = ai_provider.call_structured_with_fallback(
                config={
                    "provider_order": "openai",
                    "openai_key": "sk-" + "o" * 40,
                },
                gemini_call=lambda _key: {},
                messages=self.messages,
                schema=self.schema,
                schema_name="test_schema",
                max_tokens=128,
            )

        self.assertEqual({"status": "OK"}, parsed)
        self.assertEqual("openai", provider)
        self.assertEqual("gpt-5.6-luna", model)
        self.assertEqual(
            "https://api.openai.com/v1/chat/completions",
            post.call_args.args[0],
        )
        payload = post.call_args.kwargs["json"]
        self.assertEqual("json_schema", payload["response_format"]["type"])
        self.assertEqual(128, payload["max_completion_tokens"])
        self.assertNotIn("temperature", payload)

    def test_provider_order_can_preserve_gemini_quota(self):
        success = self.response({
            "choices": [{"message": {"content": '{"status":"OK"}'}}]
        })
        gemini = MagicMock(return_value={"status": "GEMINI"})
        with patch("ai_provider.requests.post", return_value=success):
            parsed, provider, _ = ai_provider.call_structured_with_fallback(
                config={
                    "provider_order": "groq,gemini,openrouter",
                    "gemini_key": "AIza" + "x" * 32,
                    "groq_key": "gsk_" + "y" * 32,
                },
                gemini_call=gemini,
                messages=self.messages,
                schema=self.schema,
                schema_name="test_schema",
                max_tokens=128,
            )
        self.assertEqual("groq", provider)
        self.assertEqual("OK", parsed["status"])
        gemini.assert_not_called()

    def test_non_transient_gemini_error_does_not_send_prompt_to_fallback(self):
        invalid_key = RuntimeError("invalid API key AIza" + "x" * 32)
        invalid_key.status_code = 401
        with patch("ai_provider.requests.post") as post:
            with self.assertRaises(ai_provider.AiProviderError) as ctx:
                ai_provider.call_structured_with_fallback(
                    config={
                        "gemini_key": "AIza" + "x" * 32,
                        "groq_key": "gsk_" + "y" * 32,
                    },
                    gemini_call=lambda _key: (_ for _ in ()).throw(invalid_key),
                    messages=self.messages,
                    schema=self.schema,
                    schema_name="test_schema",
                    max_tokens=128,
                )
        self.assertEqual(401, ctx.exception.status_code)
        self.assertNotIn("AIza", str(ctx.exception))
        post.assert_not_called()

    def test_explicit_order_does_not_append_unlisted_providers(self):
        quota = RuntimeError("quota")
        quota.status_code = 429
        with patch("ai_provider.requests.post") as post:
            with self.assertRaises(ai_provider.AiProviderError) as ctx:
                ai_provider.call_structured_with_fallback(
                    config={
                        "provider_order": "gemini",
                        "gemini_key": "AIza" + "x" * 32,
                        "groq_key": "gsk_" + "y" * 32,
                    },
                    gemini_call=lambda _key: (_ for _ in ()).throw(quota),
                    messages=self.messages,
                    schema=self.schema,
                    schema_name="test_schema",
                    max_tokens=128,
                )
        self.assertEqual(429, ctx.exception.status_code)
        post.assert_not_called()

    def test_bounded_content_preserves_tail_constraints(self):
        content = "opening" + ("x" * 70_000) + "FINAL CONSTRAINT"
        bounded = ai_provider._bounded_content(content)
        self.assertLessEqual(len(bounded), 60_000)
        self.assertTrue(bounded.startswith("opening"))
        self.assertTrue(bounded.endswith("FINAL CONSTRAINT"))

    def test_malformed_json_plan_is_rejected_then_falls_back(self):
        success = self.response({
            "choices": [{"message": {"content": '{"needs_clarification":false,"blocks":[{"text":"ok"}]}'}}]
        })

        def require_document_blocks(value):
            if not isinstance(value.get("blocks"), list):
                raise ValueError("blocks missing")

        with patch("ai_provider.requests.post", return_value=success) as post:
            parsed, provider, _ = ai_provider.call_structured_with_fallback(
                config={
                    "gemini_key": "AIza" + "x" * 32,
                    "groq_key": "gsk_" + "y" * 32,
                },
                gemini_call=lambda _key: {"needs_clarification": False},
                messages=self.messages,
                schema=self.schema,
                schema_name="test_schema",
                max_tokens=128,
                result_validator=require_document_blocks,
            )
        self.assertEqual("groq", provider)
        self.assertEqual("ok", parsed["blocks"][0]["text"])
        self.assertEqual(1, post.call_count)

    def test_missing_provider_configuration_is_actionable(self):
        with self.assertRaises(ai_provider.AiProviderError) as ctx:
            ai_provider.call_structured_with_fallback(
                config={},
                gemini_call=lambda _key: {},
                messages=self.messages,
                schema=self.schema,
                schema_name="test_schema",
                max_tokens=128,
            )
        self.assertEqual(503, ctx.exception.status_code)
        self.assertIn("GROQ_API_KEY", str(ctx.exception))
        self.assertIn("OPENAI_API_KEY", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
