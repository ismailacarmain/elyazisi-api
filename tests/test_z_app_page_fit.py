import unittest
from unittest.mock import patch

import ai_copilot
import ai_document
import app
from tests.test_ai_document import fake_font


class CopilotPageFitIntegrationTests(unittest.TestCase):
    def _state(self, word_count: int):
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
            "target_page_count": 1,
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


if __name__ == "__main__":
    unittest.main()
