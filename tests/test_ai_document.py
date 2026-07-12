import base64
import io
import unittest
from unittest.mock import patch

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
    def test_current_gemini_models_are_allowed(self):
        self.assertEqual("gemini-3.5-flash", ai_document.DEFAULT_GEMINI_MODEL)
        self.assertIn("gemini-3.1-flash-lite", ai_document.allowed_models())
        self.assertEqual("gemini-3.5-flash", ai_document.validate_model("gemini-3.5-flash"))

    def test_settings_are_millimetric_and_bounded(self):
        settings = ai_document.normalize_page_settings({
            "margin_left_mm": 18,
            "margin_right_mm": 16,
            "letter_height_mm": 12,
            "letter_spacing_mm": 0.5,
            "paper_type": "kareli",
            "ink_color": "#123abc",
            "paper_age": 120,
            "coffee_stains": True,
            "crease_effect": True,
            "scale_jitter": 50,
        })
        self.assertEqual("kareli", settings["paper_type"])
        self.assertEqual("#123abc", settings["ink_color"])
        self.assertAlmostEqual(18, ai_document.px_to_mm(settings["margin_left"]), places=1)
        self.assertAlmostEqual(12, ai_document.px_to_mm(settings["letter_scale"]), places=1)
        self.assertEqual(100, settings["paper_age"])
        self.assertTrue(settings["coffee_stains"])
        self.assertTrue(settings["crease_effect"])
        self.assertEqual(35, settings["scale_jitter"])

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
        self.assertTrue(clean["pages"][0]["lines"][0]["block_id"])

    def test_layout_limits_are_enforced(self):
        with self.assertRaises(ai_document.AiDocumentError):
            ai_document.validate_layout({"pages": []})
        with self.assertRaises(ai_document.AiDocumentError):
            ai_document.normalize_text("x" * (ai_document.MAX_DOCUMENT_CHARS + 1))

    def test_content_can_be_centered_on_a4(self):
        layout = ai_document.build_layout([{
            "type": "paragraph",
            "text": "abc",
            "page_break_before": False,
        }], fake_font(), {
            "horizontal_align": "center",
            "vertical_align": "center",
            "margin_left_mm": 15,
            "margin_right_mm": 15,
            "margin_top_mm": 18,
            "margin_bottom_mm": 18,
        })
        page = layout["pages"][0]
        line = page["lines"][0]
        line_center_x = line["start_x"] + line["estimated_width"] / 2
        content_center_y = (line["baseline_y"] - line["letter_scale"] + line["baseline_y"] + int(line["letter_scale"] * 0.28)) / 2
        self.assertAlmostEqual(ai_document.PAGE_WIDTH_PX / 2, line_center_x, delta=2)
        expected_y = (page["margin_top"] + ai_document.PAGE_HEIGHT_PX - page["margin_bottom"]) / 2
        self.assertAlmostEqual(expected_y, content_center_y, delta=2)

    def test_legacy_parent_document_font_map_is_decoded(self):
        image = Image.new("RGBA", (28, 50), (0, 0, 0, 0))
        ImageDraw.Draw(image).line((14, 4, 14, 46), fill=(0, 0, 0, 255), width=4)
        output = io.BytesIO()
        image.save(output, "PNG")
        encoded = base64.b64encode(output.getvalue()).decode("ascii")
        loaded = ai_document.decode_embedded_font_map({
            "kucuk_a_1": encoded,
            "kucuk_a_2": encoded,
            "buyuk_a_1": encoded,
        })
        self.assertEqual(2, len(loaded["kucuk_a"]))
        self.assertEqual(1, len(loaded["buyuk_a"]))

    def test_byok_key_precedes_server_fallback(self):
        self.assertEqual("user-key", ai_document.choose_api_key(" user-key ", "server-key"))
        self.assertEqual("server-key", ai_document.choose_api_key("", " server-key "))

    def test_block_styles_are_sanitized_and_preserved(self):
        blocks = ai_document.sanitize_blocks([{
            "type": "paragraph",
            "text": "Önemli metin",
            "page_break_before": False,
            "color": "#AABBCC",
            "align": "right",
            "scale_multiplier": 9,
            "is_margin_note": True,
        }])
        self.assertEqual("#aabbcc", blocks[0]["color"])
        self.assertEqual("right", blocks[0]["align"])
        self.assertEqual(1.6, blocks[0]["scale_multiplier"])
        self.assertTrue(blocks[0]["is_margin_note"])

    def test_margin_note_uses_right_margin_without_advancing_flow(self):
        blocks = [
            {"type": "paragraph", "text": "abc", "page_break_before": False},
            {"type": "paragraph", "text": "abc", "page_break_before": False, "is_margin_note": True},
            {"type": "paragraph", "text": "abc", "page_break_before": False},
        ]
        layout = ai_document.build_layout(blocks, fake_font(), {})
        page = layout["pages"][0]
        flow = [line for line in page["lines"] if not line.get("is_margin_note")]
        notes = [line for line in page["lines"] if line.get("is_margin_note")]
        self.assertEqual(2, len(flow))
        self.assertTrue(notes)
        self.assertGreaterEqual(notes[0]["start_x"], ai_document.PAGE_WIDTH_PX - page["margin_right"] - 150)
        self.assertEqual(ai_document.PAGE_WIDTH_PX - 24, notes[0]["max_x"])

    def test_pen_dying_effect_reaches_last_line(self):
        layout = ai_document.build_layout([{
            "type": "paragraph",
            "text": "abc " * 100,
            "page_break_before": False,
        }], fake_font(), {"pen_dying_effect": True})
        lines = [line for page in layout["pages"] for line in page["lines"]]
        self.assertGreater(len(lines), 2)
        self.assertAlmostEqual(0.95, lines[0]["opacity"], places=2)
        self.assertAlmostEqual(0.40, lines[-1]["opacity"], places=2)

    def test_response_schema_is_supported_and_well_formed(self):
        schema = ai_document._response_schema()
        self.assertEqual("OBJECT", schema["type"])
        self.assertNotIn("additionalProperties", schema)
        self.assertIn("is_margin_note", schema["properties"]["blocks"]["items"]["properties"])
        self.assertIn("author_slot", schema["properties"]["blocks"]["items"]["properties"])
        self.assertIn("coffee_stains", schema["properties"]["page_settings_override"]["properties"])
        self.assertIn("target_page_count", schema["properties"]["page_settings_override"]["properties"])

    def test_last_explicit_page_count_wins_after_clarification(self):
        self.assertEqual(1, ai_document.requested_page_count("Tek A4 sayfa olsun"))
        self.assertEqual(
            2,
            ai_document.requested_page_count(
                "1 sayfa olsun\n[Soru: Nasıl ilerleyelim? Cevabım: 2 sayfaya çıkar]"
            ),
        )
        self.assertIsNone(ai_document.requested_page_count("Kısa ve düzenli bir ödev hazırla"))

    def test_page_fit_solver_uses_largest_readable_real_metric_layout(self):
        blocks = [{"type": "paragraph", "text": "abc " * 400, "page_break_before": False}]
        result = ai_document.fit_layout_to_page_target(blocks, fake_font(), {
            "letter_height_mm": 14,
            "line_spacing_mm": 20,
            "margin_left_mm": 18,
            "margin_right_mm": 18,
            "margin_top_mm": 18,
            "margin_bottom_mm": 18,
        }, 1)
        self.assertTrue(result["success"])
        self.assertEqual(1, len(result["layout"]["pages"]))
        self.assertGreater(result["report"]["original_pages"], 1)
        self.assertEqual(1, result["report"]["actual_pages"])
        self.assertLess(
            result["report"]["settings_after"]["letter_height_mm"],
            result["report"]["settings_before"]["letter_height_mm"],
        )

    def test_compact_solver_reaches_every_documented_readability_boundary(self):
        compact = ai_document._compact_page_settings({
            "letter_height_mm": 20,
            "line_spacing_mm": 32,
            "letter_spacing_mm": 3,
            "word_spacing_mm": 12,
            "margin_left_mm": 40,
            "margin_right_mm": 40,
            "margin_top_mm": 45,
            "margin_bottom_mm": 45,
        }, 0)
        units = ai_document.normalize_page_settings(compact)["units"]
        self.assertEqual(5.5, units["letter_height_mm"])
        self.assertEqual(7.0, units["line_spacing_mm"])
        self.assertEqual(-0.7, units["letter_spacing_mm"])
        self.assertEqual(1.5, units["word_spacing_mm"])
        self.assertEqual(8.0, units["margin_left_mm"])
        self.assertEqual(8.0, units["margin_top_mm"])

    def test_page_fit_solver_expands_short_content_to_an_exact_target(self):
        blocks = [{"type": "paragraph", "text": "abc " * 20, "page_break_before": False}]
        result = ai_document.fit_layout_to_page_target(blocks, fake_font(), {
            "letter_height_mm": 11,
            "line_spacing_mm": 14,
        }, 2)
        self.assertTrue(result["success"])
        self.assertEqual(2, len(result["layout"]["pages"]))
        self.assertEqual("exact", result["report"]["constraint"])
        self.assertGreater(
            result["report"]["settings_after"]["letter_height_mm"],
            result["report"]["settings_before"]["letter_height_mm"],
        )

    def test_page_fit_solver_asks_when_content_cannot_naturally_fill_target(self):
        blocks = [{"type": "paragraph", "text": "abc", "page_break_before": False}]
        result = ai_document.fit_layout_to_page_target(blocks, fake_font(), {}, 2)
        self.assertFalse(result["success"])
        self.assertEqual("underflow", result["report"]["constraint"])
        self.assertEqual(1, result["report"]["actual_pages"])

    def test_page_fit_solver_refuses_unreadable_one_page_result(self):
        blocks = [{"type": "paragraph", "text": "abc " * 1500, "page_break_before": False}]
        result = ai_document.fit_layout_to_page_target(blocks, fake_font(), {
            "letter_height_mm": 14,
            "line_spacing_mm": 20,
        }, 1)
        self.assertFalse(result["success"])
        self.assertFalse(result["report"]["fits"])
        self.assertGreater(result["report"]["actual_pages"], 1)
        self.assertEqual(5.5, result["report"]["settings_after"]["letter_height_mm"])

    @patch("ai_document.call_gemini")
    def test_impossible_page_target_returns_clarification_instead_of_wrong_pdf(self, mock_call):
        mock_call.return_value = {
            "needs_clarification": False,
            "document_title": "Uzun Ödev",
            "blocks": [{"type": "paragraph", "text": "abc " * 1500}],
            "page_settings_override": {"target_page_count": 1},
            "summary": "Hazır",
        }
        result = ai_document.create_ai_layout(
            api_key="x" * 24,
            model="gemini-3.5-flash",
            template="odev",
            topic="Uzun bir ödev",
            instructions="Tam olarak 1 sayfa olsun",
            harfler=fake_font(),
            repetition=1,
            page_settings={"letter_height_mm": 14, "line_spacing_mm": 20},
        )
        self.assertTrue(result["needs_clarification"])
        self.assertFalse(result["fit_report"]["fits"])
        self.assertIn("akıllıca kısalt", result["clarification_options"][0])

    def test_multi_author_switches_fonts_by_block(self):
        blocks = [
            {"type": "paragraph", "text": "abc", "page_break_before": False, "author_slot": "primary"},
            {"type": "paragraph", "text": "abc", "page_break_before": False, "author_slot": "secondary"},
        ]
        layout = ai_document.build_layout(blocks, fake_font(), {"multi_author": True}, fake_font())
        slots = [line["font_slot"] for page in layout["pages"] for line in page["lines"]]
        self.assertEqual(["primary", "secondary"], slots)
        clean = ai_document.validate_layout(layout)
        self.assertEqual(["primary", "secondary"], [line["font_slot"] for line in clean["pages"][0]["lines"]])

    @patch("ai_document.call_gemini")
    def test_gemini_multi_author_requires_a_second_font(self, mock_call):
        mock_call.return_value = {
            "needs_clarification": False,
            "document_title": "Ortak çalışma",
            "blocks": [{"type": "paragraph", "text": "abc"}],
            "page_settings_override": {"multi_author": True},
        }
        with self.assertRaisesRegex(ai_document.AiDocumentError, "ikinci bir font"):
            ai_document.create_ai_layout(
                api_key="test-key",
                model="gemini-2.5-flash",
                template="odev",
                topic="İki kişi yazmış gibi hazırla",
                instructions="",
                harfler=fake_font(),
                repetition=1,
                page_settings={},
            )

    @patch("ai_document.ai_provider.call_structured_with_fallback")
    def test_document_plan_accepts_non_gemini_provider(self, mock_provider):
        mock_provider.return_value = ({
            "needs_clarification": False,
            "document_title": "Deneme",
            "blocks": [{"type": "paragraph", "text": "abc"}],
            "page_settings_override": {},
            "summary": "Hazır",
        }, "groq", "openai/gpt-oss-120b")
        result = ai_document.create_ai_layout(
            api_key=None,
            model="gemini-3.5-flash",
            template="odev",
            topic="Deneme konusu",
            instructions="",
            harfler=fake_font(),
            repetition=1,
            page_settings={},
            provider_config={"groq_key": "gsk_" + "x" * 32},
        )
        self.assertEqual("groq", result["provider"])
        self.assertEqual("openai/gpt-oss-120b", result["model"])
        self.assertTrue(result["layout"]["pages"])


if __name__ == "__main__":
    unittest.main()
