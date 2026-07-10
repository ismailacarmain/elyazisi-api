import unittest

from character_manifest import (
    BASE_KEY_SET,
    CHARACTER_MANIFEST,
    DIGIT_CHARACTERS,
    LOWERCASE_CHARACTERS,
    SYMBOL_CHARACTERS,
    UPPERCASE_CHARACTERS,
    base_key_for_character,
    validate_variation_count,
    variation_keys,
)


class CharacterManifestTests(unittest.TestCase):
    def test_manifest_has_exact_canonical_sections(self):
        self.assertEqual(32, len(LOWERCASE_CHARACTERS))
        self.assertEqual(32, len(UPPERCASE_CHARACTERS))
        self.assertEqual(10, len(DIGIT_CHARACTERS))
        self.assertEqual(33, len(SYMBOL_CHARACTERS))
        self.assertEqual(107, len(CHARACTER_MANIFEST))
        self.assertEqual(107, len(BASE_KEY_SET))
        self.assertIn('&', SYMBOL_CHARACTERS)
        self.assertIn('#', SYMBOL_CHARACTERS)

    def test_variation_key_counts_and_uniqueness(self):
        for variation_count, expected_count in ((1, 107), (3, 321), (10, 1070)):
            with self.subTest(variation_count=variation_count):
                keys = variation_keys(variation_count)
                self.assertEqual(expected_count, len(keys))
                self.assertEqual(expected_count, len(set(keys)))

    def test_turkish_and_symbol_storage_keys(self):
        expected = {
            'ç': 'kucuk_cc',
            'ğ': 'kucuk_gg',
            'ı': 'kucuk_ii',
            'İ': 'buyuk_i',
            'I': 'buyuk_ii',
            'Ö': 'buyuk_oo',
            ':': 'ozel_ikiknokta',
            ';': 'ozel_noktalivirgul',
            '_': 'ozel_alt_tire',
            '&': 'ozel_ampersand',
            '#': 'ozel_diyez',
            '\\': 'ozel_backslas',
        }
        for character, key in expected.items():
            with self.subTest(character=character):
                self.assertEqual(key, base_key_for_character(character))

    def test_only_supported_variation_counts_are_accepted(self):
        for value in (1, 2, 3, 5, 10, '1', '10'):
            with self.subTest(value=value):
                self.assertEqual(int(value), validate_variation_count(value))

        for value in (None, True, 0, 4, 6, 11, -1, 1.0, '3.0', 'abc'):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate_variation_count(value)


if __name__ == '__main__':
    unittest.main()
