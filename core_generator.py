#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core_generator.py — Fontify el yazısı render motoru v3.0
═══════════════════════════════════════════════════════════
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

from PIL import Image, ImageDraw
import os
import random
import io
import numpy as np
from character_manifest import base_key_for_character

try:
    import cv2 as _cv2
    _HAS_CV2 = True
except ImportError:
    _HAS_CV2 = False


# ══════════════════════════════════════════════════════════════════════════════
# YARDIMCI FONKSİYONLAR
# ══════════════════════════════════════════════════════════════════════════════

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
    harf_resmi = random_source.choice(harfler[anahtar]).copy()
    pixels = harf_resmi.load()
    for i in range(harf_resmi.size[0]):
        for j in range(harf_resmi.size[1]):
            r, g, b, a = pixels[i, j]
            if r < 200 and g < 200 and b < 200 and a > 0:
                dr = max(0, min(255, murekkep_rengi[0] + random_source.randint(-5, 5)))
                dg = max(0, min(255, murekkep_rengi[1] + random_source.randint(-5, 5)))
                db = max(0, min(255, murekkep_rengi[2] + random_source.randint(-5, 5)))
                pixels[i, j] = (dr, dg, db, int(a * opacity))

    if kalinlik != 0 and _HAS_CV2:
        arr   = np.array(harf_resmi)
        alpha = arr[:, :, 3]
        ks    = abs(kalinlik) + 1
        kern  = np.ones((ks, ks), np.uint8)
        if kalinlik > 0:
            alpha = _cv2.dilate(alpha, kern, iterations=kalinlik)
        else:
            alpha = _cv2.erode(alpha, kern, iterations=abs(kalinlik))
        arr[:, :, 3] = alpha
        harf_resmi = Image.fromarray(arr)

    return harf_resmi


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


# ══════════════════════════════════════════════════════════════════════════════
# FONT METRİKLERİ — AI'ın tahmin yapmasını sağlar
# ══════════════════════════════════════════════════════════════════════════════

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


# ══════════════════════════════════════════════════════════════════════════════
# KLASİK RENDER — per_line_overrides desteği
# ══════════════════════════════════════════════════════════════════════════════

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

    sayfalar = []

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
            sayfalar.append(sayfa)
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

    sayfalar.append(sayfa)
    return sayfalar


# ══════════════════════════════════════════════════════════════════════════════
# KOORDİNAT TABANLI RENDER — milimetrik hassasiyet
# ══════════════════════════════════════════════════════════════════════════════

def metni_koordinatli_yaz(layout, harfler):
    """
    AI'ın belirlediği koordinat tabanlı layout JSON'ını birebir uygular.
    Her satır için kesin baseline_y, start_x ve parametre seti.

    layout: dict  →  {"pages": [ {"paper_type": ..., "lines": [...]} ]}

    Döndürür: list[PIL.Image]  (her eleman 1 A4 sayfa, RGBA)
    """
    PAGE_W = 2480
    PAGE_H = 3508
    sayfalar = []

    for page_data in layout.get('pages', []):
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
        sayfa = cizgileri_ciz(sayfa, page_cfg)

        opacity  = page_data.get('opacity', 0.95)
        kalinlik = page_data.get('kalinlik', 0)

        for line_data in page_data.get('lines', []):
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

            x = start_x
            max_x = PAGE_W - mr

            words = text.split(' ')
            for wi, kelime in enumerate(words):
                if not kelime:
                    x += wspc // 2
                    continue

                for harf in kelime:
                    himg = harf_resmini_al(harfler, harf, ink, opacity, kalinlik, rng=line_rng)
                    if not himg:
                        continue

                    # Gürültü: letter_scale ±%1*jitter
                    noise = line_rng.uniform(-0.01 * jitt, 0.01 * jitt)
                    sized = harfi_boyutlandir(himg, max(4, int(lscale * (1 + noise))))

                    # Hafif açı gürültüsü
                    angle = line_rng.uniform(-0.2 * jitt, 0.2 * jitt)
                    rot   = sized.rotate(angle, resample=Image.BICUBIC, expand=True)
                    gw, gh = rot.size

                    # Taşma koruma — max_x'i geçme
                    if x + gw > max_x:
                        break

                    # EXACT Y hesabı:
                    # baseline_y: harfin ALT hizası (baseline)
                    # Harfi baseline'a göre hizala
                    slope_dy = (x - start_x) * slope
                    rand_dy  = line_rng.uniform(-jitt, jitt) * 0.4
                    final_y  = int(baseline_y - lscale + slope_dy + loff + rand_dy + off_y)

                    sayfa.paste(rot, (x, final_y), rot)
                    x += gw + lspc + line_rng.randint(0, 3)

                if wi < len(words) - 1:
                    x += wspc

        sayfalar.append(sayfa)

    return sayfalar


# ══════════════════════════════════════════════════════════════════════════════
# PDF ÇIKIŞI
# ══════════════════════════════════════════════════════════════════════════════

def sayfalari_pdf_olustur(sayfalar):
    if not sayfalar:
        return None
    rgb_list = []
    for s in sayfalar:
        rgb = Image.new('RGB', s.size, (255, 255, 255))
        rgb.paste(s, mask=s.split()[3])
        rgb_list.append(rgb)
    buf = io.BytesIO()
    rgb_list[0].save(
        buf, 'PDF', resolution=300.0,
        save_all=True, append_images=rgb_list[1:], quality=95
    )
    buf.seek(0)
    return buf
