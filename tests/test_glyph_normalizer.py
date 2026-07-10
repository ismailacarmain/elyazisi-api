import base64
import io
import unittest

from PIL import Image, ImageDraw

from glyph_normalizer import GlyphValidationError, normalize_digital_glyph


def encode_image(image, image_format='PNG', data_url=False):
    output = io.BytesIO()
    image.save(output, format=image_format)
    encoded = base64.b64encode(output.getvalue()).decode('ascii')
    if data_url:
        mime = 'jpeg' if image_format.upper() == 'JPEG' else 'png'
        return f'data:image/{mime};base64,{encoded}'
    return encoded


def decode_normalized(value):
    return Image.open(io.BytesIO(base64.b64decode(value))).convert('RGBA')


class GlyphNormalizerTests(unittest.TestCase):
    def test_transparent_canvas_becomes_tight_black_rgba(self):
        source = Image.new('RGBA', (900, 700), (0, 0, 0, 0))
        draw = ImageDraw.Draw(source)
        draw.line((300, 500, 450, 130, 600, 500), fill=(20, 30, 50, 255), width=35)

        normalized = decode_normalized(
            normalize_digital_glyph(encode_image(source, data_url=True))
        )

        self.assertEqual('RGBA', normalized.mode)
        self.assertLess(normalized.width, source.width)
        self.assertLess(normalized.height, source.height)
        self.assertLessEqual(max(normalized.size), 512)
        alpha = normalized.getchannel('A')
        self.assertIsNotNone(alpha.getbbox())
        colors = normalized.getcolors(maxcolors=normalized.width * normalized.height)
        opaque_colors = [color for _, color in colors if color[3] > 0]
        self.assertTrue(opaque_colors)
        self.assertTrue(all(color[:3] == (0, 0, 0) for color in opaque_colors))

    def test_white_jpeg_background_becomes_alpha(self):
        source = Image.new('RGB', (240, 180), 'white')
        draw = ImageDraw.Draw(source)
        draw.ellipse((85, 35, 155, 145), outline='black', width=14)

        normalized = decode_normalized(
            normalize_digital_glyph(encode_image(source, 'JPEG'))
        )

        alpha = normalized.getchannel('A')
        self.assertIsNotNone(alpha.getbbox())
        self.assertEqual(0, alpha.getpixel((0, 0)))
        self.assertLess(normalized.width, source.width)
        self.assertLess(normalized.height, source.height)

    def test_transparent_and_white_blank_canvases_are_rejected(self):
        blank_images = (
            Image.new('RGBA', (120, 120), (0, 0, 0, 0)),
            Image.new('RGB', (120, 120), 'white'),
        )
        for image in blank_images:
            with self.subTest(mode=image.mode):
                with self.assertRaises(GlyphValidationError):
                    normalize_digital_glyph(encode_image(image))

    def test_non_image_and_invalid_data_url_are_rejected(self):
        invalid_values = (
            base64.b64encode(b'not an image').decode('ascii'),
            'data:text/plain;base64,SGVsbG8=',
            'not-base64!',
        )
        for value in invalid_values:
            with self.subTest(value=value[:20]):
                with self.assertRaises(GlyphValidationError):
                    normalize_digital_glyph(value)


if __name__ == '__main__':
    unittest.main()
