import unittest
from unittest.mock import patch

import ai_copilot
import ai_document
import app
from tests.test_ai_document import fake_font


class CopilotPageFitIntegrationTests(unittest.TestCase):
    def _state(self, word_count: int, target_pages: int = 1):
        blocks = [{"type": "paragraph", "text": "abc " * word_count, "page_break_before": False}]
        settings = {"letter_height_mm": 14, "line_spacing_mm": 20}
        layout = ai_document.build_layout(blocks, fake_font(), settings)
        layout, blocks = ai_copilot.ensure_document_ids(layout, blocks)
        doc = {
            "user_id": "user-a",
            "font_id": "font-a",
            "secondary_font_id": "",
            "page_settings": settings,
            "version": 1,
        }
        result = {
            "needs_clarification": False,
            "new_layout": layout,
            "new_blocks": blocks,
            "operations": [],
            "inverse_operations": [],
            "reflow_needed": False,
            "target_page_count": target_pages,
            "page_target_intent": "exact",
            "assistant_message": "",
        }
        return doc, result

    @patch("app._load_font_images", return_value=fake_font())
    @patch("app._font_access_for_user", return_value=(object(), {}))
    def test_copilot_one_page_command_uses_deterministic_fit_and_is_undoable(self, _access, _load):
        doc, result = self._state(400)
        finalized = app._finalize_copilot_result(doc, result)
        self.assertFalse(finalized["needs_clarification"])
        self.assertEqual(1, len(finalized["new_layout"]["pages"]))
        self.assertTrue(finalized["fit_report"]["fits"])
        self.assertTrue(finalized["operations"])
        self.assertTrue(finalized["inverse_operations"])
        self.assertIn("gerçek font ölçüleriyle", finalized["assistant_message"])

    @patch("app._load_font_images", return_value=fake_font())
    @patch("app._font_access_for_user", return_value=(object(), {}))
    def test_copilot_asks_before_breaking_readability(self, _access, _load):
        doc, result = self._state(1500)
        finalized = app._finalize_copilot_result(doc, result)
        self.assertTrue(finalized["needs_clarification"])
        self.assertFalse(finalized["fit_report"]["fits"])
        self.assertEqual([], finalized["operations"])
        self.assertIn("akıllıca kısalt", finalized["clarification_options"][0])

    @patch("app._load_font_images", return_value=fake_font())
    @patch("app._font_access_for_user", return_value=(object(), {}))
    def test_copilot_asks_to_expand_content_for_an_underfilled_target(self, _access, _load):
        doc, result = self._state(1, target_pages=2)
        finalized = app._finalize_copilot_result(doc, result)
        self.assertTrue(finalized["needs_clarification"])
        self.assertEqual("underflow", finalized["fit_report"]["constraint"])
        self.assertIn("genişlet", finalized["clarification_options"][0])

    @patch("app._load_font_images", return_value=fake_font())
    @patch("app._font_access_for_user", return_value=(object(), {}))
    def test_saved_page_target_is_reapplied_on_a_later_copilot_edit(self, _access, _load):
        doc, result = self._state(400)
        fitted = app._finalize_copilot_result(doc, result)
        doc["page_settings"] = fitted["page_settings_update"]
        doc["layout"] = fitted["new_layout"]
        doc["blocks"] = fitted["new_blocks"]
        follow_up = {
            "needs_clarification": False,
            "new_layout": doc["layout"],
            "new_blocks": doc["blocks"],
            "operations": [],
            "inverse_operations": [],
            "reflow_needed": True,
            "page_target_intent": "",
            "assistant_message": "",
        }
        finalized = app._finalize_copilot_result(doc, follow_up)
        self.assertFalse(finalized["needs_clarification"])
        self.assertEqual(1, len(finalized["new_layout"]["pages"]))

    def test_manual_target_intent_clears_a_saved_target(self):
        doc, result = self._state(20)
        doc["page_settings"]["target_page_count"] = 1
        result["page_target_intent"] = "manual"
        finalized = app._finalize_copilot_result(doc, result)
        self.assertNotIn("target_page_count", finalized["page_settings_update"])

    def test_partial_client_settings_keep_a_saved_page_target(self):
        settings = app._effective_page_settings(
            {},
            {"letter_height_mm": 12},
            {"letter_height_mm": 11, "target_page_count": 1},
        )
        self.assertEqual(1, settings["target_page_count"])
        self.assertEqual(12, settings["letter_height_mm"])

    def test_server_manual_snapshot_can_clear_a_saved_page_target(self):
        settings = app._effective_page_settings(
            {"page_settings_update": {"letter_height_mm": 12}},
            None,
            {"letter_height_mm": 11, "target_page_count": 1},
        )
        self.assertNotIn("target_page_count", settings)
        self.assertEqual(12, settings["letter_height_mm"])

    def test_manual_state_preflight_allows_cross_origin_patch(self):
        response = app.app.test_client().open(
            "/api/ai/documents/example/state",
            method="OPTIONS",
            headers={
                "Origin": "https://fontify.online",
                "Access-Control-Request-Method": "PATCH",
            },
        )
        self.assertEqual(204, response.status_code)
        self.assertIn("PATCH", response.headers.get("Access-Control-Allow-Methods", ""))

    @patch("app._store.create_document", return_value="document-1")
    @patch("app.auth.verify_id_token", return_value={"uid": "user-a"})
    def test_initial_copilot_document_persists_flat_validated_settings(self, _auth, create):
        blocks = [{"type": "paragraph", "text": "Deneme metni.", "page_break_before": False}]
        layout = ai_document.build_layout(blocks, fake_font(), {"letter_height_mm": 12})
        response = app.app.test_client().post(
            "/api/ai/documents",
            headers={"Authorization": "Bearer test-token"},
            json={
                "layout": layout,
                "blocks": blocks,
                "page_settings": {"letter_height_mm": 12, "target_page_count": 1},
            },
        )
        self.assertEqual(200, response.status_code)
        persisted = create.call_args.kwargs["page_settings"]
        self.assertEqual(12, persisted["letter_height_mm"])
        self.assertEqual(1, persisted["target_page_count"])
        self.assertNotIn("units", persisted)


if __name__ == "__main__":
    unittest.main()
