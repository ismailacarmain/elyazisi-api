import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageDraw

import ai_document
import core_generator


def effect_font():
    result = {}
    for key in ("kucuk_a", "kucuk_b", "kucuk_c"):
        image = Image.new("RGBA", (42, 72), (0, 0, 0, 0))
        ImageDraw.Draw(image).line((5, 64, 21, 7, 37, 64), fill=(0, 0, 0, 255), width=5)
        result[key] = [image]
    return result


class CoreGeneratorEffectsTests(unittest.TestCase):
    def test_thickened_transparent_edges_use_ink_colour_not_white_halo(self):
        if not core_generator._HAS_CV2:
            self.skipTest("OpenCV is not installed")
        glyph = Image.new("RGBA", (24, 24), (255, 255, 255, 0))
        ImageDraw.Draw(glyph).rectangle((10, 5, 13, 18), fill=(0, 0, 0, 255))
        font = {"kucuk_a": [glyph]}

        rendered = core_generator.harf_resmini_al(
            font, "a", (20, 55, 110), opacity=1.0, kalinlik=2,
            rng=__import__("random").Random(42),
        )

        pixels = np.asarray(rendered)
        newly_visible = (pixels[:, :, 3] > 0) & (np.asarray(glyph)[:, :, 3] == 0)
        self.assertTrue(np.any(newly_visible))
        edge_rgb = pixels[:, :, :3][newly_visible]
        self.assertLess(int(edge_rgb.max()), 130)
        self.assertGreater(int(edge_rgb[:, 2].mean()), int(edge_rgb[:, 0].mean()))

    def test_page_dying_pen_fades_even_when_lines_have_opacity(self):
        lines = [{"opacity": 0.95}, {"opacity": 0.95}, {"opacity": 0.95}]
        values = [
            core_generator._effective_line_opacity(line, 0.95, index, len(lines), True)
            for index, line in enumerate(lines)
        ]
        self.assertAlmostEqual(0.95, values[0], places=2)
        self.assertLess(values[1], values[0])
        self.assertAlmostEqual(0.40, values[-1], places=2)

    def test_markdown_spans_are_parsed(self):
        styled = list(core_generator._styled_words("==a b== **c** ~~a~~"))
        self.assertEqual([
            ("a", "highlight"),
            ("b", "highlight"),
            ("c", "underline"),
            ("a", "strikethrough"),
        ], styled)

    def test_markdown_effects_render_without_pillow_mode_errors(self):
        layout = {
            "pages": [{
                "paper_type": "duz",
                "margin_top": 120,
                "margin_left": 120,
                "margin_right": 120,
                "line_spacing": 140,
                "lines": [{
                    "text": "==a== **b** ~~c~~",
                    "baseline_y": 260,
                    "start_x": 140,
                    "letter_scale": 90,
                    "letter_spacing": 2,
                    "word_spacing": 35,
                    "line_slope": 0,
                    "jitter": 0,
                    "ink_color": "#1b1b1d",
                    "seed": 123,
                }],
            }],
        }
        page = next(core_generator.metni_koordinatli_yaz(layout, effect_font()))
        pixels = np.asarray(page)
        yellow = (pixels[:, :, 0] > 200) & (pixels[:, :, 1] > 160) & (pixels[:, :, 2] < 120)
        red = (pixels[:, :, 0] > 170) & (pixels[:, :, 1] < 100) & (pixels[:, :, 2] < 100)
        self.assertTrue(np.any(yellow))
        self.assertTrue(np.any(red))

    def test_paper_effects_are_deterministic(self):
        page = Image.new("RGBA", (500, 700), (255, 255, 255, 255))
        config = {"paper_age": 70, "coffee_stains": True, "crease_effect": True}
        first = core_generator.kagit_efektlerini_uygula(page, config, seed=77)
        second = core_generator.kagit_efektlerini_uygula(page, config, seed=77)
        self.assertEqual(first.tobytes(), second.tobytes())
        self.assertNotEqual(first.getpixel((10, 10)), (255, 255, 255, 255))

    def test_scale_jitter_is_deterministic_but_changes_render(self):
        base_line = {
            "text": "abcabc",
            "baseline_y": 260,
            "start_x": 140,
            "letter_scale": 90,
            "letter_spacing": 2,
            "word_spacing": 35,
            "line_slope": 0,
            "jitter": 0,
            "ink_color": "#1b1b1d",
            "seed": 999,
        }
        layout = {"pages": [{"paper_type": "duz", "margin_top": 120, "margin_left": 120, "margin_right": 120, "line_spacing": 140, "lines": [{**base_line, "scale_jitter": 30}]}]}
        jittered_a = next(core_generator.metni_koordinatli_yaz(layout, effect_font()))
        jittered_b = next(core_generator.metni_koordinatli_yaz(layout, effect_font()))
        plain_layout = {"pages": [{"paper_type": "duz", "margin_top": 120, "margin_left": 120, "margin_right": 120, "line_spacing": 140, "lines": [{**base_line, "scale_jitter": 0}]}]}
        plain = next(core_generator.metni_koordinatli_yaz(plain_layout, effect_font()))
        self.assertEqual(jittered_a.tobytes(), jittered_b.tobytes())
        self.assertNotEqual(jittered_a.tobytes(), plain.tobytes())

    def test_horizontal_overflow_compresses_instead_of_dropping_glyphs(self):
        layout = {"pages": [{
            "paper_type": "duz",
            "margin_top": 120,
            "margin_left": 120,
            "margin_right": 120,
            "line_spacing": 140,
            "lines": [{
                "text": "abc" * 10,
                "baseline_y": 300,
                "start_x": 120,
                "max_x": 420,
                "letter_scale": 120,
                "letter_spacing": 20,
                "word_spacing": 35,
                "line_slope": 0,
                "jitter": 15,
                "scale_jitter": 35,
                "ink_color": "#1b1b1d",
                "seed": 321,
            }],
        }]}
        pasted = []
        original = Image.Image.paste

        def track_paste(target, image, box=None, mask=None):
            if target.size == (2480, 3508) and isinstance(box, tuple):
                pasted.append((box[0], image.width))
            return original(target, image, box, mask)

        with patch.object(Image.Image, "paste", track_paste):
            next(core_generator.metni_koordinatli_yaz(layout, effect_font()))

        self.assertEqual(30, len(pasted))
        self.assertTrue(all(x >= 120 and x + width <= 420 for x, width in pasted))

    def test_validator_clamps_extreme_line_controls_inside_a4(self):
        layout = ai_document.validate_layout({"pages": [{
            "id": "extreme",
            "paper_type": "duz",
            "margin_top": 60,
            "margin_left": 60,
            "margin_right": 60,
            "margin_bottom": 60,
            "line_spacing": 70,
            "opacity": 1,
            "kalinlik": 4,
            "scale_jitter": 35,
            "lines": [{
                "id": "extreme-line",
                "block_id": "block-1",
                "text": "abcabc",
                "baseline_y": 3448,
                "start_x": 60,
                "max_x": 2420,
                "estimated_width": 1200,
                "letter_scale": 260,
                "letter_spacing": 2,
                "word_spacing": 10,
                "line_slope": 20,
                "jitter": 15,
                "scale_jitter": 35,
                "ink_color": "#111111",
                "line_offset_y": 120,
                "seed": 12345,
            }],
        }]})
        line = layout["pages"][0]["lines"][0]
        self.assertLess(line["baseline_y"], 3448)

        boxes = []
        original = Image.Image.paste

        def track_paste(target, image, box=None, mask=None):
            if target.size == (2480, 3508) and isinstance(box, tuple):
                boxes.append((box[0], box[1], image.width, image.height))
            return original(target, image, box, mask)

        with patch.object(Image.Image, "paste", track_paste):
            next(core_generator.metni_koordinatli_yaz(layout, effect_font()))

        self.assertEqual(6, len(boxes))
        self.assertTrue(all(
            x >= 0 and y >= 0 and x + width <= 2480 and y + height <= 3508
            for x, y, width, height in boxes
        ))


if __name__ == "__main__":
    unittest.main()
