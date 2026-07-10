"""Validation and canonicalization for paperless handwritten glyph uploads."""

from __future__ import annotations

import base64
import binascii
import io
import re
from typing import Final

import numpy as np
from PIL import Image, UnidentifiedImageError


MAX_GLYPH_INPUT_BYTES: Final[int] = 2 * 1024 * 1024
MAX_GLYPH_DIMENSION: Final[int] = 2048
MAX_GLYPH_PIXELS: Final[int] = 4_000_000
MAX_CANONICAL_DIMENSION: Final[int] = 512
MAX_CANONICAL_PNG_BYTES: Final[int] = 2 * 1024 * 1024
_DATA_URL_RE: Final[re.Pattern[str]] = re.compile(
    r"^data:(image/(?:png|jpeg|jpg));base64,(.*)$",
    re.IGNORECASE | re.DOTALL,
)


class GlyphValidationError(ValueError):
    """The supplied value is not a usable handwritten glyph."""


class GlyphTooLargeError(GlyphValidationError):
    """The supplied or normalized glyph exceeds a safe resource limit."""


def _decode_image(value: object) -> bytes:
    if not isinstance(value, str) or not value.strip():
        raise GlyphValidationError("Harf görseli boş veya geçersiz.")

    encoded = value.strip()
    match = _DATA_URL_RE.fullmatch(encoded)
    if match:
        encoded = match.group(2)
    elif encoded.lower().startswith("data:"):
        raise GlyphValidationError("Yalnızca base64 PNG veya JPEG data URL kabul edilir.")

    # JSON encoders occasionally wrap long raw base64 values.  Whitespace is
    # harmless, while validate=True still rejects every non-base64 character.
    encoded = re.sub(r"\s+", "", encoded)
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise GlyphValidationError("Harf görselinin base64 verisi geçersiz.") from exc

    if not raw:
        raise GlyphValidationError("Harf görseli boş.")
    if len(raw) > MAX_GLYPH_INPUT_BYTES:
        raise GlyphTooLargeError("Harf görseli en fazla 2 MB olabilir.")
    return raw


def _load_image(raw: bytes) -> Image.Image:
    try:
        with Image.open(io.BytesIO(raw)) as probe:
            image_format = (probe.format or "").upper()
            width, height = probe.size
            frames = getattr(probe, "n_frames", 1)
            if image_format not in {"PNG", "JPEG", "JPG"}:
                raise GlyphValidationError("Harf görseli PNG veya JPEG olmalıdır.")
            if frames != 1:
                raise GlyphValidationError("Animasyonlu harf görselleri desteklenmiyor.")
            if width < 1 or height < 1:
                raise GlyphValidationError("Harf görselinin boyutları geçersiz.")
            if (
                width > MAX_GLYPH_DIMENSION
                or height > MAX_GLYPH_DIMENSION
                or width * height > MAX_GLYPH_PIXELS
            ):
                raise GlyphTooLargeError(
                    "Harf görseli en fazla 2048×2048 ve 4 megapiksel olabilir."
                )
            probe.load()
            return probe.convert("RGBA")
    except GlyphValidationError:
        raise
    except Image.DecompressionBombError as exc:
        raise GlyphTooLargeError("Harf görselinin piksel boyutu çok büyük.") from exc
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise GlyphValidationError("Harf görseli okunamadı.") from exc


def _ink_alpha(image: Image.Image) -> np.ndarray:
    rgba = np.asarray(image, dtype=np.uint8)
    rgb = rgba[:, :, :3].astype(np.float32)
    source_alpha = rgba[:, :, 3].astype(np.float32) / 255.0

    # Composite-to-white luminance converts both common canvas exports into the
    # same mask: transparent black strokes and dark strokes on white PNG/JPEG.
    luminance = (
        0.2126 * rgb[:, :, 0]
        + 0.7152 * rgb[:, :, 1]
        + 0.0722 * rgb[:, :, 2]
    )
    darkness = (255.0 - luminance) * source_alpha
    mask = np.clip(np.rint(darkness), 0, 255).astype(np.uint8)

    # Ignore compression dust and almost-invisible accidental pencil touches,
    # but preserve antialiasing around a real stroke.
    mask[mask < 8] = 0
    return mask


def normalize_digital_glyph(value: object) -> str:
    """Return a tight, black RGBA PNG as raw base64.

    Accepted input is raw base64 or a PNG/JPEG data URL.  Empty canvases are
    rejected, white/transparent backgrounds become alpha, and output is bounded
    to 512 px so Firestore and Render memory usage stay predictable.
    """

    image = _load_image(_decode_image(value))
    mask = _ink_alpha(image)
    visible = np.argwhere(mask > 0)
    if visible.size == 0:
        raise GlyphValidationError("Boş harf kabul edilmez; lütfen harfi çiziniz.")

    y_min, x_min = visible.min(axis=0)
    y_max, x_max = visible.max(axis=0)
    stroke_width = int(x_max - x_min + 1)
    stroke_height = int(y_max - y_min + 1)
    if stroke_width < 2 or stroke_height < 2 or visible.shape[0] < 4:
        raise GlyphValidationError("Çizim harf olarak algılanamayacak kadar küçük.")

    padding = max(3, int(round(max(stroke_width, stroke_height) * 0.04)))
    cropped = mask[y_min : y_max + 1, x_min : x_max + 1]
    padded = np.pad(cropped, padding, mode="constant", constant_values=0)
    canonical = Image.fromarray(padded, mode="L")

    if max(canonical.size) > MAX_CANONICAL_DIMENSION:
        scale = MAX_CANONICAL_DIMENSION / max(canonical.size)
        resized = (
            max(1, int(round(canonical.width * scale))),
            max(1, int(round(canonical.height * scale))),
        )
        canonical = canonical.resize(resized, Image.Resampling.LANCZOS)

    alpha = np.asarray(canonical, dtype=np.uint8)
    output = np.zeros((canonical.height, canonical.width, 4), dtype=np.uint8)
    output[:, :, 3] = alpha
    rgba = Image.fromarray(output, mode="RGBA")

    buffer = io.BytesIO()
    rgba.save(buffer, format="PNG", optimize=True, compress_level=9)
    png = buffer.getvalue()
    if len(png) > MAX_CANONICAL_PNG_BYTES:
        raise GlyphTooLargeError("Normalize edilmiş harf görseli çok büyük.")
    return base64.b64encode(png).decode("ascii")
