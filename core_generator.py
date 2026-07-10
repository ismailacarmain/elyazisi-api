#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core_generator.py — Fontify el yazısı render motoru
Sürüm 2.0: per-line parametreler (letter_spacing, line_slope, jitter, ink_color, vb.) desteklenir.
"""
from PIL import Image, ImageDraw
import os
import random
import io
import cv2
import numpy as np

from character_manifest import base_key_for_character


# ─── YARDIMCI FONKSİYONLAR ───────────────────────────────────────────────────

def _hex_to_rgb(hex_color, default=(27, 27, 29)):
    """'#rrggbb' veya '#rgb' → (r, g, b)"""
    try:
        h = hex_color.lstrip('#')
        if len(h) == 3:
            h = ''.join(c * 2 for c in h)
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
    except Exception:
        return default


def karakter_anahtarini_bul(karakter):
    try:
        return base_key_for_character(karakter, allow_space=True)
    except ValueError:
        return None


def harf_resimlerini_yukle(klasor_yolu="static/harfler"):
    harfler = {}
    if not os.path.exists(klasor_yolu):
        print(f"⚠️  UYARI: {klasor_yolu} bulunamadı!")
        return harfler
    for dosya in os.listdir(klasor_yolu):
        if dosya.endswith('.png'):
            parts = dosya.replace('.png', '').split('_')
            if len(parts) >= 2:
                tip = parts[0]
                karakter = '_'.join(parts[1:-1]) if len(parts) > 2 else parts[1]
                anahtar = f"{tip}_{karakter}"
                dosya_yolu = os.path.join(klasor_yolu, dosya)
                try:
                    resim = Image.open(dosya_yolu).convert("RGBA")
                    if anahtar not in harfler:
                        harfler[anahtar] = []
                    harfler[anahtar].append(resim)
                except Exception as e:
                    print(f"Hata: {dosya} yüklenemedi - {e}")
    return harfler


def harf_resmini_al(harfler, karakter, murekkep_rengi=(27, 27, 29), opacity=0.95, kalinlik=0):
    anahtar = karakter_anahtarini_bul(karakter)
    if anahtar and anahtar in harfler:
        harf_resmi = random.choice(harfler[anahtar]).copy()
        pixels = harf_resmi.load()
        for i in range(harf_resmi.size[0]):
            for j in range(harf_resmi.size[1]):
                r, g, b, a = pixels[i, j]
                if r < 128 and g < 128 and b < 128 and a > 200:
                    dither_r = max(0, min(255, murekkep_rengi[0] + random.randint(-5, 5)))
                    dither_g = max(0, min(255, murekkep_rengi[1] + random.randint(-5, 5)))
                    dither_b = max(0, min(255, murekkep_rengi[2] + random.randint(-5, 5)))
                    new_alpha = int(a * opacity)
                    pixels[i, j] = (dither_r, dither_g, dither_b, new_alpha)
        if kalinlik != 0:
            img_array = np.array(harf_resmi)
            alpha = img_array[:, :, 3]
            kernel_size = abs(kalinlik) + 1
            kernel = np.ones((kernel_size, kernel_size), np.uint8)
            if kalinlik > 0:
                alpha = cv2.dilate(alpha, kernel, iterations=kalinlik)
            else:
                alpha = cv2.erode(alpha, kernel, iterations=abs(kalinlik))
            img_array[:, :, 3] = alpha
            harf_resmi = Image.fromarray(img_array)
        return harf_resmi
    return None


def harfi_boyutlandir(harf_resmi, hedef_yukseklik):
    orijinal_genislik, orijinal_yukseklik = harf_resmi.size
    if orijinal_yukseklik == 0:
        return harf_resmi
    oran = hedef_yukseklik / orijinal_yukseklik
    yeni_genislik = max(1, int(orijinal_genislik * oran))
    yeni_yukseklik = max(1, int(hedef_yukseklik))
    return harf_resmi.resize((yeni_genislik, yeni_yukseklik), Image.Resampling.LANCZOS)


def cizgileri_ciz(sayfa, config):
    paper_type = config.get('paper_type', 'cizgili')
    if paper_type == 'duz':
        return sayfa
    draw = ImageDraw.Draw(sayfa)
    width, height = sayfa.size
    line_spacing = config['line_spacing']
    margin_top = config['margin_top']
    line_color = (135, 206, 250, 100)
    line_width = 3
    y = margin_top
    while y < height - 100:
        draw.line([(0, y), (width, y)], fill=line_color, width=line_width)
        y += line_spacing
    if paper_type == 'kareli':
        grid_size = line_spacing
        x = config['margin_left']
        while x < width:
            draw.line([(x, 0), (x, height)], fill=line_color, width=line_width)
            x += grid_size
        x = config['margin_left']
        while x > 0:
            x -= grid_size
            draw.line([(x, 0), (x, height)], fill=line_color, width=line_width)
    return sayfa


def yeni_sayfa_olustur(page_width, page_height, print_background, background_path=None):
    if print_background and background_path and os.path.exists(background_path):
        try:
            arka_plan = Image.open(background_path).convert("RGBA")
            if arka_plan.size != (page_width, page_height):
                arka_plan = arka_plan.resize((page_width, page_height), Image.Resampling.LANCZOS)
            return arka_plan.copy()
        except Exception:
            pass
    return Image.new("RGBA", (page_width, page_height), (255, 255, 255, 255))


# ─── ANA RENDER FONKSİYONU ───────────────────────────────────────────────────

def metni_sayfaya_yaz(metin, harfler, config, per_line_overrides=None):
    """
    Metni el yazısı fontlarıyla sayfalara yazar.

    Parametreler
    ────────────
    metin : str
        Yazdırılacak metin. Satırlar '\\n' ile ayrılır.
    harfler : dict
        {anahtar: [PIL.Image, ...]} sözlüğü.
    config : dict
        Global sayfa ayarları. Zorunlu anahtarlar:
          page_width, page_height, margin_top, margin_left, margin_right,
          target_letter_height, line_spacing, word_spacing
        Opsiyonel: paper_type, murekkep_rengi, opacity, jitter, line_slope,
                   letter_spacing, kalinlik, print_background, background_path
    per_line_overrides : dict[int | str, dict]  (opsiyonel)
        Satır indeksine göre (0-tabanlı) per-satır parametre overrides.
        Desteklenen override anahtarları:
          letter_scale   (int)   – hedef harf yüksekliği (piksel)
          letter_spacing (int)   – harfler arası ek piksel (negatif olabilir)
          word_spacing   (int)   – kelimeler arası ek piksel
          line_slope     (float) – eğim yoğunluğu (0=düz, 10=belirgin)
          jitter         (int)   – doğallık / titreme (0-15)
          ink_color      (str)   – '#rrggbb' biçiminde mürekkep rengi
          line_offset_y  (int)   – satırı dikey kaydır (piksel, + = aşağı)

    Döndürür
    ────────
    list[PIL.Image]  – Her eleman bir A4 sayfa (RGBA)
    """
    if per_line_overrides is None:
        per_line_overrides = {}
    # JSON string-key gelirse int'e çevir
    per_line_overrides = {int(k): v for k, v in per_line_overrides.items()}

    # ── Global parametreler ──
    jitter_global   = config.get('jitter', 3)
    global_ink      = config.get('murekkep_rengi', (27, 27, 29))
    global_opacity  = config.get('opacity', 0.95)
    global_kalinlik = config.get('kalinlik', 0)
    global_lspacing = config.get('letter_spacing', 0)

    sayfalar = []

    def create_page():
        p = yeni_sayfa_olustur(
            config['page_width'], config['page_height'],
            config.get('print_background', False),
            config.get('background_path')
        )
        return cizgileri_ciz(p, config)

    sayfa = create_page()
    current_line = 0
    x = config['margin_left']
    max_x = config['page_width'] - config['margin_right']
    max_y = config['page_height'] - 200

    # ── Yardımcı: satır eğim parametrelerini hesapla ──
    def get_line_params(idx):
        ov = per_line_overrides.get(idx, {})
        slope_factor = float(ov.get('line_slope', config.get('line_slope', 5)))
        random.seed(idx + 555)
        slope  = (random.random() - 0.5) * (slope_factor * 0.0005)
        offset = (random.random() - 0.5) * (slope_factor * 2.0)
        random.seed()
        return slope, offset

    # ── Yardımcı: satıra özgü tüm parametreler ──
    def get_line_cfg(idx):
        ov        = per_line_overrides.get(idx, {})
        lscale    = int(ov.get('letter_scale',   config['target_letter_height']))
        lspacing  = int(ov.get('letter_spacing', global_lspacing))
        wspacing  = int(ov.get('word_spacing',   config.get('word_spacing', 55)))
        jitt      = int(ov.get('jitter',         jitter_global))
        off_y     = int(ov.get('line_offset_y',  0))
        ink_hex   = ov.get('ink_color', None)
        ink       = _hex_to_rgb(ink_hex) if ink_hex else global_ink
        return lscale, lspacing, wspacing, jitt, off_y, ink

    # ── Sayfa ve satır başlatma ──
    line_slope, line_offset = get_line_params(current_line)
    y_base = config['margin_top'] + (current_line * config['line_spacing']) - config['target_letter_height']

    def advance_line():
        """Sonraki satıra geç, gerekirse yeni sayfa aç."""
        nonlocal sayfa, current_line, x, line_slope, line_offset, y_base
        x = config['margin_left']
        current_line += 1
        line_slope, line_offset = get_line_params(current_line)
        y_base = config['margin_top'] + (current_line * config['line_spacing']) - config['target_letter_height']
        if y_base + config['target_letter_height'] > max_y:
            sayfalar.append(sayfa)
            sayfa = create_page()
            current_line = 0
            line_slope, line_offset = get_line_params(current_line)
            y_base = config['margin_top'] + (current_line * config['line_spacing']) - config['target_letter_height']

    # ── Metin döngüsü ──
    for satir in metin.split('\n'):
        if not satir.strip():
            # Boş satır → sadece satır atla
            advance_line()
            continue

        lscale, lspacing, wspacing, jitt, off_y, ink_rgb = get_line_cfg(current_line)

        for kelime in satir.split(' '):
            if not kelime:
                x += wspacing // 2
                continue

            # Satır sonunu tahmin et
            tahmini_w = len(kelime) * (lscale // 2 + lspacing + 4)
            if x + tahmini_w > max_x and x > config['margin_left']:
                advance_line()
                lscale, lspacing, wspacing, jitt, off_y, ink_rgb = get_line_cfg(current_line)

            for harf in kelime:
                harf_resmi = harf_resmini_al(harfler, harf, ink_rgb, global_opacity, global_kalinlik)
                if not harf_resmi:
                    continue

                # Ölçek ve açı gürültüsü
                scale_noise = random.uniform(-0.01 * jitt, 0.01 * jitt)
                sized = harfi_boyutlandir(harf_resmi, max(4, int(lscale * (1 + scale_noise))))
                angle = random.uniform(-0.2 * jitt, 0.2 * jitt)
                rotated = sized.rotate(angle, resample=Image.BICUBIC, expand=True)

                gw, gh = rotated.size

                # Satır sonu kontrolü (harf genişliği sonra anlaşıldı)
                if x + gw > max_x:
                    advance_line()
                    lscale, lspacing, wspacing, jitt, off_y, ink_rgb = get_line_cfg(current_line)

                slope_y  = (x - config['margin_left']) * line_slope
                random_y = random.uniform(-jitt, jitt) * 0.5
                final_y  = int(y_base + slope_y + line_offset + random_y + off_y
                               - (gh - lscale) / 2)

                sayfa.paste(rotated, (x, final_y), rotated)
                x += gw + lspacing + random.randint(0, 4)

            x += wspacing  # kelimeler arası boşluk

        advance_line()  # satır sonu

    sayfalar.append(sayfa)
    return sayfalar


# ─── PDF OLUŞTURMA ───────────────────────────────────────────────────────────

def sayfalari_pdf_olustur(sayfalar):
    if not sayfalar:
        return None
    rgb_sayfalar = []
    for sayfa in sayfalar:
        rgb = Image.new('RGB', sayfa.size, (255, 255, 255))
        rgb.paste(sayfa, mask=sayfa.split()[3])
        rgb_sayfalar.append(rgb)
    buf = io.BytesIO()
    rgb_sayfalar[0].save(
        buf, 'PDF', resolution=300.0,
        save_all=True, append_images=rgb_sayfalar[1:], quality=95
    )
    buf.seek(0)
    return buf
