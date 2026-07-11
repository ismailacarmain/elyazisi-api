import unittest

import numpy as np
from PIL import Image, ImageDraw

import core_generator


def effect_font():
    result = {}
    for key in ("kucuk_a", "kucuk_b", "kucuk_c"):
        image = Image.new("RGBA", (42, 72), (0, 0, 0, 0))
        ImageDraw.Draw(image).line((5, 64, 21, 7, 37, 64), fill=(0, 0, 0, 255), width=5)
        result[key] = [image]
    return result


class CoreGeneratorEffectsTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
