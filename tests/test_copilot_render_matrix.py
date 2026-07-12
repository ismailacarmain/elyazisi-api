import hashlib
import unittest

import ai_copilot
import ai_document
import core_generator
from tests.test_ai_document import fake_font


def rendered_hash(layout):
    validated = ai_document.validate_layout(layout)
    page = next(core_generator.metni_koordinatli_yaz(validated, fake_font()))
    return hashlib.sha256(page.tobytes()).hexdigest()


class CopilotRenderMatrixTests(unittest.TestCase):
    def _document(self, text="abc abc abc"):
        blocks = [{
            "id": "block-1",
            "type": "paragraph",
            "text": text,
            "page_break_before": False,
        }]
        layout = ai_document.build_layout(blocks, fake_font(), {
            "paper_type": "duz",
            "jitter": 0,
            "line_slope": 0,
        })
        return ai_copilot.ensure_document_ids(layout, blocks)

    def test_block_slope_changes_validated_pdf_pixels(self):
        layout, blocks = self._document()
        before = rendered_hash(layout)
        operations = ai_copilot.validate_and_sanitize_operations([{
            "operation": "update_block_style",
            "target_id": "block-1",
            "patch": {"line_slope": 18},
        }], layout, blocks)
        changed, _, _ = ai_copilot.apply_operations(operations, layout, blocks)
        self.assertNotEqual(before, rendered_hash(changed))

    def test_page_dying_pen_changes_existing_line_opacity_in_pdf(self):
        layout, blocks = self._document("abc " * 140)
        before = rendered_hash(layout)
        operations = ai_copilot.validate_and_sanitize_operations([{
            "operation": "update_page_settings",
            "target_id": "page-1",
            "patch": {"pen_dying_effect": True},
        }], layout, blocks)
        changed, _, _ = ai_copilot.apply_operations(operations, layout, blocks)
        self.assertNotEqual(before, rendered_hash(changed))

    def test_line_controls_survive_api_layout_validation(self):
        layout, blocks = self._document()
        line_id = layout["pages"][0]["lines"][0]["id"]
        operations = ai_copilot.validate_and_sanitize_operations([{
            "operation": "update_line_style",
            "target_id": line_id,
            "patch": {
                "opacity": 0.4,
                "kalinlik": 4,
                "jitter": 15,
                "scale_jitter": 35,
                "line_slope": 20,
                "letter_spacing": 42,
                "word_spacing": 10,
                "line_offset_y": 120,
            },
        }], layout, blocks)
        changed, _, _ = ai_copilot.apply_operations(operations, layout, blocks)
        validated = ai_document.validate_layout(changed)
        line = validated["pages"][0]["lines"][0]
        self.assertEqual(0.4, line["opacity"])
        self.assertEqual(4, line["kalinlik"])
        self.assertEqual(15, line["jitter"])
        self.assertEqual(35, line["scale_jitter"])
        self.assertEqual(20, line["line_slope"])
        self.assertEqual(42, line["letter_spacing"])
        self.assertEqual(10, line["word_spacing"])
        self.assertEqual(120, line["line_offset_y"])


if __name__ == "__main__":
    unittest.main()
