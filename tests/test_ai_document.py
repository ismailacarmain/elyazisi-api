import unittest

from PIL import Image, ImageDraw

import ai_document


def fake_font():
    result = {}
    for key, width in (
        ("kucuk_a", 34), ("kucuk_b", 37), ("kucuk_c", 32),
        ("kucuk_m", 55), ("buyuk_a", 48), ("rakam_1", 24),
        ("ozel_tire", 28),
    ):
        image = Image.new("RGBA", (width, 80), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.line((4, 70, width // 2, 8, width - 4, 70), fill=(0, 0, 0, 255), width=5)
        result[key] = [image]
    return result


class AiDocumentTests(unittest.TestCase):
    def test_settings_are_millimetric_and_bounded(self):
        settings = ai_document.normalize_page_settings({
            "margin_left_mm": 18,
            "margin_right_mm": 16,
            "letter_height_mm": 12,
            "letter_spacing_mm": 0.5,
            "paper_type": "kareli",
            "ink_color": "#123abc",
        })
        self.assertEqual("kareli", settings["paper_type"])
        self.assertEqual("#123abc", settings["ink_color"])
        self.assertAlmostEqual(18, ai_document.px_to_mm(settings["margin_left"]), places=1)
        self.assertAlmostEqual(12, ai_document.px_to_mm(settings["letter_scale"]), places=1)

    def test_real_metrics_drive_wrapping_and_coordinates(self):
        blocks = [{
            "type": "paragraph",
            "text": "abc " * 220,
            "page_break_before": False,
        }]
        layout = ai_document.build_layout(blocks, fake_font(), {
            "margin_left_mm": 20,
            "margin_right_mm": 20,
            "margin_top_mm": 18,
            "margin_bottom_mm": 18,
            "letter_height_mm": 11,
            "line_spacing_mm": 14,
        })
        self.assertGreaterEqual(len(layout["pages"]), 2)
        for page in layout["pages"]:
            right = ai_document.PAGE_WIDTH_PX - page["margin_right"]
            bottom = ai_document.PAGE_HEIGHT_PX - page["margin_bottom"]
            for line in page["lines"]:
                self.assertGreaterEqual(line["start_x"], page["margin_left"])
                self.assertLessEqual(line["baseline_y"], bottom)
                self.assertLessEqual(line["start_x"] + line["estimated_width"], right + 1)

    def test_manual_blocks_and_layout_validation(self):
        blocks = ai_document.manual_blocks("İlk paragraf.\n\n- Bir\n- İki", "Başlık")
        self.assertEqual("title", blocks[0]["type"])
        self.assertEqual(2, sum(1 for block in blocks if block["type"] == "list_item"))
        layout = ai_document.build_layout(blocks, fake_font(), {})
        clean = ai_document.validate_layout(layout)
        self.assertEqual("A4", clean["page_size"])
        self.assertTrue(clean["pages"][0]["lines"])

    def test_layout_limits_are_enforced(self):
        with self.assertRaises(ai_document.AiDocumentError):
            ai_document.validate_layout({"pages": []})
        with self.assertRaises(ai_document.AiDocumentError):
            ai_document.normalize_text("x" * (ai_document.MAX_DOCUMENT_CHARS + 1))


if __name__ == "__main__":
    unittest.main()

