#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core_generator.py — Fontify el yazısı render motoru v3.0
==============================================================================
İKİ RENDER MODU:

1. metni_sayfaya_yaz(metin, harfler, config, per_line_overrides=None)
   Klasik akış render — per-satır parametre override desteğiyle.

2. metni_koordinatli_yaz(layout, harfler)
   Koordinat tabanlı render — AI'ın milimetrik hassasiyetle verdiği
   satır bazlı layout JSON'ını birebir uygular.
   Layout JSON şeması:
   {
     "pages": [
       {
         "paper_type": "cizgili",       # zorunlu
         "margin_top":  220,             # zorunlu
         "margin_left": 180,             # yalnızca çizgi çizimi için
         "line_spacing": 215,            # yalnızca çizgi çizimi için
         "lines": [
           {
             "text":           "Osmanlı İmparatorluğu",
             "baseline_y":     220,       # EXACT harf baseline Y (px)
             "start_x":        180,       # ilk harfin sol kenarı X (px)
             "letter_scale":   135,       # hedef harf yüksekliği (px)
             "letter_spacing": 3,         # harfler arası ek px (+ sağa açar)
             "word_spacing":   55,        # kelime arası ek px
             "line_slope":     3.0,       # eğim yoğunluğu
             "jitter":         4,         # titreme
             "ink_color":      "#1b1b1d", # mürekkep rengi
             "line_offset_y":  0          # tüm satırı Y ekseninde kaydır
           }
         ]
       }
     ]
   }

3. get_font_metrics(harfler, letter_scale)
   Verilen ölçekte her karakterin GERÇEK ortalama genişliğini döndürür.
   → AI bu tabloyu kullanarak satır genişliğini ÖNCEDEN hesaplayabilir.
"""

from PIL import Image, ImageDraw, ImageFilter
import os
import random
import io
import re
import hashlib
import numpy as np
from character_manifest import base_key_for_character

try:
    import cv2 as _cv2
    _HAS_CV2 = True
except ImportError:
    _HAS_CV2 = False


# ==============================================================================
# YARDIMCI FONKSİYONLAR
# ==============================================================================

def _hex_to_rgb(hex_color, default=(27, 27, 29)):
    """'#rrggbb' veya '#rgb'  →  (r, g, b)"""
    try:
        h = hex_color.lstrip('#')
        if len(h) == 3:
            h = ''.join(c * 2 for c in h)
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except Exception:
        return default


def karakter_anahtarini_bul(karakter):
    try:
        return base_key_for_character(karakter, allow_space=True)
    except ValueError:
        return None


def harf_resimlerini_yukle(klasor_yolu="static/harfler"):
    """Klasör tabanlı yükleme (eski uyumluluk)."""
    harfler = {}
    if not os.path.exists(klasor_yolu):
        return harfler
    for dosya in os.listdir(klasor_yolu):
        if dosya.endswith('.png'):
            parts = dosya.replace('.png', '').split('_')
            if len(parts) >= 2:
                tip = parts[0]
                kar = '_'.join(parts[1:-1]) if len(parts) > 2 else parts[1]
                key = f"{tip}_{kar}"
                try:
                    img = Image.open(os.path.join(klasor_yolu, dosya)).convert("RGBA")
                    harfler.setdefault(key, []).append(img)
                except Exception:
                    pass
    return harfler


def harf_resmini_al(harfler, karakter, murekkep_rengi=(27, 27, 29), opacity=0.95, kalinlik=0, rng=None):
    """Rastgele varyasyon seç, renklendirip döndür."""
    anahtar = karakter_anahtarini_bul(karakter)
    if not (anahtar and anahtar in harfler):
        return None

    random_source = rng or random
    harf_resmi = random_source.choice(harfler[anahtar]).copy().convert("RGBA")
    arr = np.array(harf_resmi, dtype=np.uint8)
    original_alpha = arr[:, :, 3].copy()
    ink_mask = (
        (arr[:, :, 0] < 200)
        & (arr[:, :, 1] < 200)
        & (arr[:, :, 2] < 200)
        & (arr[:, :, 3] > 0)
    )
    opacity = max(0.0, min(1.0, float(opacity)))
    noise_rng = np.random.default_rng(random_source.randint(0, 2_000_000_000))
    noise = noise_rng.integers(-5, 6, size=arr[:, :, :3].shape, dtype=np.int16)
    base = np.asarray(murekkep_rengi, dtype=np.int16).reshape((1, 1, 3))
    coloured = np.clip(base + noise, 0, 255).astype(np.uint8)
    if np.any(ink_mask):
        arr[:, :, :3][ink_mask] = coloured[ink_mask]
        alpha = arr[:, :, 3].astype(np.float32)
        alpha[ink_mask] *= opacity
        arr[:, :, 3] = np.clip(alpha, 0, 255).astype(np.uint8)

    if kalinlik != 0 and _HAS_CV2:
        alpha = arr[:, :, 3]
        ks    = abs(kalinlik) + 1
        kern  = np.ones((ks, ks), np.uint8)
        if kalinlik > 0:
            alpha = _cv2.dilate(alpha, kern, iterations=kalinlik)
        else:
            alpha = _cv2.erode(alpha, kern, iterations=abs(kalinlik))
        arr[:, :, 3] = alpha
        # Dilation makes formerly transparent edge pixels visible. Their RGB
        # payload is often white in exported glyph PNGs; leaving it untouched
        # creates a pale halo around a thick pen stroke. Colour every newly
        # revealed pixel with the same deterministic ink noise as the glyph.
        newly_visible = (alpha > 0) & (original_alpha == 0)
        if np.any(newly_visible):
            arr[:, :, :3][newly_visible] = coloured[newly_visible]

    return Image.fromarray(arr)


def _styled_words(text):
    """Yield (word, style) pairs for ==highlight==, **underline** and ~~strike~~ spans."""
    active_style = None
    active_marker = None
    markers = (("==", "highlight"), ("**", "underline"), ("__", "underline"), ("~~", "strikethrough"))
    for raw_word in str(text or "").split():
        word = raw_word
        style = active_style
        if active_style is None:
            for marker, marker_style in markers:
                if word.startswith(marker):
                    active_marker = marker
                    active_style = marker_style
                    style = marker_style
                    word = word[len(marker):]
                    break
        if active_style and active_marker:
            close_index = word.find(active_marker)
            if close_index >= 0:
                word = word[:close_index] + word[close_index + len(active_marker):]
                style = active_style
                active_style = None
                active_marker = None
        if word:
            yield word, style


def _effective_line_opacity(line_data, page_opacity, line_index, line_count, pen_dying_effect):
    line_opacity = float(line_data.get('opacity', page_opacity))
    if not pen_dying_effect:
        return line_opacity
    progress = line_index / max(1, line_count - 1)
    dying_opacity = max(
        0.40,
        float(page_opacity) - progress * max(0.0, float(page_opacity) - 0.40),
    )
    return min(line_opacity, dying_opacity)


def harfi_boyutlandir(harf_resmi, hedef_yukseklik):
    ow, oh = harf_resmi.size
    if oh == 0:
        return harf_resmi
    nw = max(1, int(ow * hedef_yukseklik / oh))
    nh = max(1, int(hedef_yukseklik))
    return harf_resmi.resize((nw, nh), Image.Resampling.LANCZOS)


def cizgileri_ciz(sayfa, config):
    pt = config.get('paper_type', 'cizgili')
    if pt == 'duz':
        return sayfa
    draw   = ImageDraw.Draw(sayfa)
    w, h   = sayfa.size
    ls     = config['line_spacing']
    mt     = config['margin_top']
    color  = (135, 206, 250, 100)
    y = mt
    while y < h - 100:
        draw.line([(0, y), (w, y)], fill=color, width=3)
        y += ls
    if pt == 'kareli':
        ml = config.get('margin_left', 0)
        x  = ml
        while x < w:
            draw.line([(x, 0), (x, h)], fill=color, width=3)
            x += ls
        x = ml
        while x > 0:
            x -= ls
            draw.line([(x, 0), (x, h)], fill=color, width=3)
    return sayfa


def yeni_sayfa_olustur(pw, ph, print_bg=False, bg_path=None):
    if print_bg and bg_path and os.path.exists(bg_path):
        try:
            img = Image.open(bg_path).convert("RGBA")
            if img.size != (pw, ph):
                img = img.resize((pw, ph), Image.Resampling.LANCZOS)
            return img.copy()
        except Exception:
            pass
    return Image.new("RGBA", (pw, ph), (255, 255, 255, 255))


def kagit_efektlerini_uygula(sayfa, config, seed=1):
    """Apply deterministic, print-safe aging, coffee rings and fold creases."""
    age = max(0, min(100, int(config.get("paper_age", 0) or 0)))
    coffee = config.get("coffee_stains") is True
    creases = config.get("crease_effect") is True
    if age <= 0 and not coffee and not creases:
        return sayfa

    width, height = sayfa.size
    rng = random.Random(int(seed))
    base = (
        max(205, 255 - round(age * 0.28)),
        max(195, 255 - round(age * 0.42)),
        max(175, 255 - round(age * 0.65)),
        255,
    )
    result = Image.new("RGBA", (width, height), base)

    if age > 0:
        noise_rng = np.random.default_rng(int(seed) & 0xFFFFFFFF)
        small_w, small_h = max(32, width // 24), max(44, height // 24)
        noise = noise_rng.normal(128, 24, (small_h, small_w)).clip(0, 255).astype(np.uint8)
        texture = Image.fromarray(noise).resize((width, height), Image.Resampling.BICUBIC)
        texture_alpha = texture.point(lambda value: int(abs(value - 128) * age / 220))
        texture_layer = Image.new("RGBA", (width, height), (116, 83, 42, 0))
        texture_layer.putalpha(texture_alpha)
        result = Image.alpha_composite(result, texture_layer)

    if coffee:
        stain_layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(stain_layer, "RGBA")
        for _ in range(2 + (1 if age > 55 else 0)):
            radius_x = rng.randint(max(90, width // 18), max(150, width // 8))
            radius_y = int(radius_x * rng.uniform(0.65, 1.15))
            center_x = rng.choice((rng.randint(30, width // 3), rng.randint(width * 2 // 3, width - 30)))
            center_y = rng.randint(height // 8, height * 7 // 8)
            box = (center_x - radius_x, center_y - radius_y, center_x + radius_x, center_y + radius_y)
            for ring in range(5):
                inset = ring * 3
                alpha = max(12, 46 - ring * 7)
                draw.ellipse(
                    (box[0] + inset, box[1] + inset, box[2] - inset, box[3] - inset),
                    outline=(126, 72, 27, alpha),
                    width=max(2, 7 - ring),
                )
        stain_layer = stain_layer.filter(ImageFilter.GaussianBlur(radius=1.2))
        result = Image.alpha_composite(result, stain_layer)

    if creases:
        crease_layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(crease_layer, "RGBA")
        for position, vertical in ((width // 2 + rng.randint(-80, 80), True), (height // 2 + rng.randint(-100, 100), False)):
            if vertical:
                draw.line((position - 3, 0, position - 3, height), fill=(78, 59, 38, 22), width=5)
                draw.line((position + 3, 0, position + 3, height), fill=(255, 255, 245, 42), width=4)
            else:
                draw.line((0, position - 3, width, position - 3), fill=(78, 59, 38, 20), width=5)
                draw.line((0, position + 3, width, position + 3), fill=(255, 255, 245, 38), width=4)
        result = Image.alpha_composite(result, crease_layer.filter(ImageFilter.GaussianBlur(radius=0.8)))

    return result


# ==============================================================================
# FONT METRİKLERİ — AI'ın tahmin yapmasını sağlar
# ==============================================================================

def get_font_metrics(harfler, letter_scale=135):
    """
    Verilen ölçekte her karakterin GERÇEK ortalama genişliğini döndürür.

    Döndürür:
    {
      "kucuk_a": {"avg_w": 42, "avg_h": 48, "variants": 3},
      "buyuk_A": {"avg_w": 58, "avg_h": 68, "variants": 1},
      ...
      "_summary": {
        "avg_char_w": 45,
        "avg_char_h": 52,
        "total_chars": 107,
        "scale": 135
      }
    }
    """
    metrics = {}
    total_w  = 0
    total_h  = 0
    count    = 0

    for key, variants in harfler.items():
        ws, hs = [], []
        for img in variants:
            ow, oh = img.size
            if oh == 0:
                continue
            nw = max(1, int(ow * letter_scale / oh))
            ws.append(nw)
            hs.append(letter_scale)

        if not ws:
            continue

        avg_w = round(sum(ws) / len(ws))
        avg_h = letter_scale
        metrics[key] = {
            "avg_w":    avg_w,
            "avg_h":    avg_h,
            "variants": len(variants)
        }
        total_w += avg_w
        total_h += avg_h
        count   += 1

    if count > 0:
        metrics["_summary"] = {
            "avg_char_w": round(total_w / count),
            "avg_char_h": letter_scale,
            "total_chars": count,
            "scale": letter_scale
        }
    return metrics


def estimate_line_width(text, metrics, letter_spacing=0, word_spacing=55):
    """
    Bir metin satırının tahmini piksel genişliğini hesaplar.

    Parametreler
    ────────────
    text          : str   – Ölçülecek metin
    metrics       : dict  – get_font_metrics() çıktısı
    letter_spacing: int   – Harfler arası ek piksel
    word_spacing  : int   – Kelimeler arası piksel

    Döndürür: (estimated_px: int, char_count: int, word_count: int)
    """
    from character_manifest import base_key_for_character

    avg_default = metrics.get('_summary', {}).get('avg_char_w', 45)
    total = 0
    words = text.split(' ')
    word_count = len(words)

    for wi, word in enumerate(words):
        for ch in word:
            try:
                key = base_key_for_character(ch, allow_space=False)
                cw  = metrics.get(key, {}).get('avg_w', avg_default)
            except ValueError:
                cw = avg_default
            total += cw + letter_spacing

        if wi < word_count - 1:
            total += word_spacing

    return total, len(text.replace(' ', '')), word_count


# ==============================================================================
# KLASİK RENDER — per_line_overrides desteği
# ==============================================================================

def metni_sayfaya_yaz(metin, harfler, config, per_line_overrides=None):
    """
    Klasik satır-akış render.

    per_line_overrides: {int_idx: {param: val}}
      Desteklenen: letter_scale, letter_spacing, word_spacing,
                   line_slope, jitter, ink_color, line_offset_y
    """
    if per_line_overrides is None:
        per_line_overrides = {}
    plo = {int(k): v for k, v in per_line_overrides.items()}

    jitter_g  = config.get('jitter', 3)
    global_ink = config.get('murekkep_rengi', (27, 27, 29))
    opacity    = config.get('opacity', 0.95)
    kalinlik   = config.get('kalinlik', 0)
    global_ls  = config.get('letter_spacing', 0)

    

    def make_page():
        p = yeni_sayfa_olustur(
            config['page_width'], config['page_height'],
            config.get('print_background', False),
            config.get('background_path')
        )
        return cizgileri_ciz(p, config)

    sayfa = make_page()
    cur_line = 0
    x = config['margin_left']
    max_x = config['page_width'] - config['margin_right']
    max_y = config['page_height'] - 200

    def line_slope_offset(idx):
        ov = plo.get(idx, {})
        sf = float(ov.get('line_slope', config.get('line_slope', 5)))
        random.seed(idx + 555)
        slope  = (random.random() - 0.5) * (sf * 0.0005)
        offset = (random.random() - 0.5) * (sf * 2.0)
        random.seed()
        return slope, offset

    def line_cfg(idx):
        ov      = plo.get(idx, {})
        lscale  = int(ov.get('letter_scale',   config['target_letter_height']))
        lspc    = int(ov.get('letter_spacing',  global_ls))
        wspc    = int(ov.get('word_spacing',    config.get('word_spacing', 55)))
        jitt    = int(ov.get('jitter',          jitter_g))
        off_y   = int(ov.get('line_offset_y',   0))
        ink_hex = ov.get('ink_color', None)
        ink     = _hex_to_rgb(ink_hex) if ink_hex else global_ink
        return lscale, lspc, wspc, jitt, off_y, ink

    lslope, loffset = line_slope_offset(cur_line)
    y_base = config['margin_top'] + cur_line * config['line_spacing'] - config['target_letter_height']

    def advance():
        nonlocal sayfa, cur_line, x, lslope, loffset, y_base
        x = config['margin_left']
        cur_line += 1
        lslope, loffset = line_slope_offset(cur_line)
        y_base = config['margin_top'] + cur_line * config['line_spacing'] - config['target_letter_height']
        if y_base + config['target_letter_height'] > max_y:
            yield sayfa
            sayfa = make_page()
            cur_line = 0
            lslope, loffset = line_slope_offset(cur_line)
            y_base = config['margin_top'] - config['target_letter_height']

    for satir in metin.split('\n'):
        if not satir.strip():
            advance()
            continue

        lscale, lspc, wspc, jitt, off_y, ink = line_cfg(cur_line)

        for wi, kelime in enumerate(satir.split(' ')):
            if not kelime:
                x += wspc // 2
                continue
            est_w = len(kelime) * (lscale // 2 + lspc + 4)
            if x + est_w > max_x and x > config['margin_left']:
                advance()
                lscale, lspc, wspc, jitt, off_y, ink = line_cfg(cur_line)

            for harf in kelime:
                himg = harf_resmini_al(harfler, harf, ink, opacity, kalinlik)
                if not himg:
                    continue
                noise = random.uniform(-0.01 * jitt, 0.01 * jitt)
                sized = harfi_boyutlandir(himg, max(4, int(lscale * (1 + noise))))
                angle = random.uniform(-0.2 * jitt, 0.2 * jitt)
                rot   = sized.rotate(angle, resample=Image.BICUBIC, expand=True)
                gw, gh = rot.size
                if x + gw > max_x:
                    advance()
                    lscale, lspc, wspc, jitt, off_y, ink = line_cfg(cur_line)

                sy    = (x - config['margin_left']) * lslope
                ry    = random.uniform(-jitt, jitt) * 0.5
                fy    = int(y_base + sy + loffset + ry + off_y - (gh - lscale) / 2)
                sayfa.paste(rot, (x, fy), rot)
                x += gw + lspc + random.randint(0, 4)

            x += wspc

        advance()

    yield sayfa
    


# ──────────────────────────────────────────────────────────────────────────
# KOORDİNAT TABANLI RENDER — milimetrik hassasiyet
# ──────────────────────────────────────────────────────────────────────────

def metni_koordinatli_yaz(layout, harfler, font_sets=None):
    """
    AI'ın belirlediği koordinat tabanlı layout JSON'ını birebir uygular.
    Her satır için kesin baseline_y, start_x ve parametre seti.

    layout: dict  →  {"pages": [ {"paper_type": ..., "lines": [...]} ]}

    Döndürür: list[PIL.Image]  (her eleman 1 A4 sayfa, RGBA)
    """
    PAGE_W = 2480
    PAGE_H = 3508
    

    available_fonts = {'primary': harfler}
    if isinstance(font_sets, dict):
        available_fonts.update({key: value for key, value in font_sets.items() if isinstance(value, dict) and value})

    for page_index, page_data in enumerate(layout.get('pages', [])):
        pt  = page_data.get('paper_type', 'cizgili')
        mt  = page_data.get('margin_top',   220)
        ml  = page_data.get('margin_left',  180)
        mr  = page_data.get('margin_right', 180)
        ls  = page_data.get('line_spacing', 215)

        page_cfg = {
            'page_width':  PAGE_W,
            'page_height': PAGE_H,
            'margin_top':  mt,
            'margin_left': ml,
            'line_spacing': ls,
            'paper_type':  pt,
        }
        sayfa = yeni_sayfa_olustur(PAGE_W, PAGE_H)
        page_seed_text = str(page_data.get('id', page_index + 1)).encode('utf-8', 'replace')
        page_seed = int(hashlib.sha256(page_seed_text).hexdigest()[:8], 16)
        sayfa = kagit_efektlerini_uygula(sayfa, page_data, page_seed)
        sayfa = cizgileri_ciz(sayfa, page_cfg)

        page_opacity  = page_data.get('opacity', 0.95)
        page_kalinlik = page_data.get('kalinlik', 0)
        page_lines = page_data.get('lines', [])
        pen_dying_effect = page_data.get('pen_dying_effect') is True

        for line_index, line_data in enumerate(page_lines):
            line_opacity = _effective_line_opacity(
                line_data, page_opacity, line_index, len(page_lines), pen_dying_effect
            )
            line_kalinlik = line_data.get('kalinlik', page_kalinlik)
            line_scale_jitter = max(0.0, min(35.0, float(line_data.get('scale_jitter', page_data.get('scale_jitter', 0)) or 0)))
            font_slot = 'secondary' if line_data.get('font_slot') == 'secondary' else 'primary'
            line_font = available_fonts.get(font_slot, harfler)

            text        = line_data.get('text', '')
            baseline_y  = int(line_data.get('baseline_y', mt))
            start_x     = int(line_data.get('start_x', ml))
            lscale      = int(line_data.get('letter_scale', 135))
            lspc        = int(line_data.get('letter_spacing', 0))
            wspc        = int(line_data.get('word_spacing', 55))
            slope_f     = float(line_data.get('line_slope', 3))
            jitt        = int(line_data.get('jitter', 4))
            off_y       = int(line_data.get('line_offset_y', 0))
            ink_hex     = line_data.get('ink_color', '#1b1b1d')
            ink         = _hex_to_rgb(ink_hex)

            # Aynı layout her önizlemede birebir aynı varyasyon ve jitter'ı üretir.
            line_rng = random.Random(int(line_data.get('seed', 10_000)))
            slope  = (line_rng.random() - 0.5) * (slope_f * 0.0005)
            loff   = (line_rng.random() - 0.5) * (slope_f * 1.5)

            max_x = int(line_data.get('max_x', PAGE_W - mr))
            max_x = max(start_x + 1, min(PAGE_W, max_x))

            words = list(_styled_words(text))
            natural_x = 0
            word_runs = []
            for wi, (kelime, word_style) in enumerate(words):
                word_start_x = natural_x
                rendered_glyphs = []

                for harf in kelime:
                    himg = harf_resmini_al(line_font, harf, ink, line_opacity, line_kalinlik, rng=line_rng)
                    if not himg:
                        continue

                    # Harf boyutu rastgeleliği, konum/eğim jitter'ından bağımsızdır.
                    noise = line_rng.uniform(-line_scale_jitter / 100.0, line_scale_jitter / 100.0)
                    sized = harfi_boyutlandir(himg, max(4, int(lscale * (1 + noise))))

                    # Hafif açı gürültüsü
                    angle = line_rng.uniform(-0.2 * jitt, 0.2 * jitt)
                    rot   = sized.rotate(angle, resample=Image.BICUBIC, expand=True)
                    gw, _ = rot.size
                    rand_dy  = line_rng.uniform(-jitt, jitt) * 0.4
                    rendered_glyphs.append((rot, natural_x, rand_dy))
                    natural_x += gw + lspc + line_rng.randint(0, 3)

                word_runs.append((word_style, word_start_x, natural_x, rendered_glyphs))
                if wi < len(words) - 1:
                    natural_x += wspc

            # Raster variation, line-level spacing edits and rotation can make
            # the real glyph run slightly wider than the metric estimate used
            # during wrapping.  Compress only the horizontal geometry when
            # necessary; never discard the tail of a word.
            available_width = max(1, max_x - start_x)
            natural_width = max(1, natural_x)
            horizontal_scale = min(1.0, available_width / natural_width)
            draw = ImageDraw.Draw(sayfa, "RGBA")

            for word_style, natural_start, natural_end, rendered_glyphs in word_runs:
                is_highlight = word_style == "highlight"
                is_underline = word_style == "underline"
                is_strikethrough = word_style == "strikethrough"
                word_start_x = start_x + int(round(natural_start * horizontal_scale))
                word_end_x = start_x + int(round(natural_end * horizontal_scale))

                if is_highlight and word_end_x > word_start_x:
                    draw.rectangle(
                        [word_start_x - 3, baseline_y - int(lscale * 0.9), word_end_x + 3, baseline_y + int(lscale * 0.15)],
                        fill=(255, 224, 64, 72),
                    )

                for glyph, glyph_natural_x, rand_dy in rendered_glyphs:
                    glyph_x = min(
                        max_x - 1,
                        start_x + int(round(glyph_natural_x * horizontal_scale)),
                    )
                    if horizontal_scale < 1.0:
                        scaled_width = max(1, int(round(glyph.width * horizontal_scale)))
                        scaled_width = max(1, min(scaled_width, max_x - glyph_x))
                        if scaled_width != glyph.width:
                            glyph = glyph.resize((scaled_width, glyph.height), Image.Resampling.LANCZOS)
                    slope_dy = (glyph_x - start_x) * slope
                    glyph_y = int(baseline_y - lscale + slope_dy + loff + rand_dy + off_y)
                    glyph_y = max(0, min(PAGE_H - glyph.height, glyph_y))
                    sayfa.paste(glyph, (glyph_x, glyph_y), glyph)

                if is_underline or is_strikethrough:
                    line_y = baseline_y + int(lscale * 0.1) if is_underline else baseline_y - int(lscale * 0.45)
                    color = (220, 20, 20, int(line_opacity * 255)) if is_underline else (ink[0], ink[1], ink[2], int(line_opacity * 255))
                    curr_x = word_start_x
                    curr_y = line_y
                    while curr_x < word_end_x:
                        next_x = min(curr_x + line_rng.randint(8, 20), word_end_x)
                        next_y = line_y + line_rng.randint(-3, 3)
                        draw.line([(curr_x, curr_y), (next_x, next_y)], fill=color, width=max(2, int(line_kalinlik) + 2))
                        curr_x = next_x
                        curr_y = next_y

        yield sayfa

    


# ==============================================================================
# PDF ÇIKIŞI
# ==============================================================================

def sayfalari_pdf_olustur(sayfalar):
    first_rgb = None
    rgb_list = []
    
    for s in sayfalar:
        # Bellek tasarrufu için A4 boyutunu 150 DPI eşdeğerine düşür
        new_size = (s.width // 2, s.height // 2)
        try:
            s.thumbnail(new_size, Image.Resampling.LANCZOS)
        except AttributeError:
            s.thumbnail(new_size, Image.LANCZOS)
            
        rgb = Image.new('RGB', s.size, (255, 255, 255))
        rgb.paste(s, mask=s.split()[3])
        s.close()
        
        if first_rgb is None:
            first_rgb = rgb
        else:
            rgb_list.append(rgb)
            
    if not first_rgb:
        return None
        
    buf = io.BytesIO()
    first_rgb.save(
        buf, 'PDF', resolution=150.0,
        save_all=True, append_images=rgb_list, quality=85
    )
    buf.seek(0)
    return buf


