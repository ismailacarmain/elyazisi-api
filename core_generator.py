#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from PIL import Image, ImageDraw
import os
import random
import io
import cv2
import numpy as np

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

def karakter_anahtarini_bul(karakter):
    ozel_karakterler = {
        ' ': 'ozel_bosluk', '.': 'ozel_nokta', ',': 'ozel_virgul',
        ':': 'ozel_ikiknokta', ';': 'ozel_noktalivirgul', '?': 'ozel_soru',
        '!': 'ozel_unlem', '-': 'ozel_tire', '(': 'ozel_parantezac', ')': 'ozel_parantezkapama',
    }
    if karakter in ozel_karakterler: return ozel_karakterler[karakter]
    elif karakter.isdigit(): return f"rakam_{karakter}"
    elif karakter.isupper(): return f"buyuk_{karakter}"
    elif karakter.islower(): return f"kucuk_{karakter}"
    return None

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
            if kalinlik > 0: alpha = cv2.dilate(alpha, kernel, iterations=kalinlik) 
            else: alpha = cv2.erode(alpha, kernel, iterations=abs(kalinlik))
            img_array[:, :, 3] = alpha
            harf_resmi = Image.fromarray(img_array)
        return harf_resmi
    return None

def harfi_boyutlandir(harf_resmi, hedef_yukseklik):
    orijinal_genislik, orijinal_yukseklik = harf_resmi.size
    if orijinal_yukseklik == 0: return harf_resmi
    oran = hedef_yukseklik / orijinal_yukseklik
    yeni_genislik = int(orijinal_genislik * oran)
    yeni_yukseklik = int(hedef_yukseklik)
    return harf_resmi.resize((yeni_genislik, yeni_yukseklik), Image.Resampling.LANCZOS)

def cizgileri_ciz(sayfa, config):
    paper_type = config.get('paper_type', 'cizgili')
    if paper_type == 'duz': return sayfa
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
        except: pass
    return Image.new("RGBA", (page_width, page_height), (255, 255, 255, 255))

def metni_sayfaya_yaz(metin, harfler, config):
    sayfalar = []
    jitter = config.get('jitter', 3)
    def create_page():
        p = yeni_sayfa_olustur(config['page_width'], config['page_height'], config.get('print_background', False), config.get('background_path'))
        return cizgileri_ciz(p, config)
    
    sayfa = create_page()
    current_line = 0
    x = config['margin_left']
    max_x = config['page_width'] - config['margin_right']
    max_y = config['page_height'] - 200
    
    def get_line_params(idx):
        random.seed(idx + 555)
        slope = (random.random() - 0.5) * (config.get('line_slope', 5) * 0.0005)
        offset = (random.random() - 0.5) * (config.get('line_slope', 5) * 2)
        random.seed()
        return slope, offset

    line_slope, line_offset = get_line_params(current_line)
    y_base = config['margin_top'] + (current_line * config['line_spacing']) - config['target_letter_height']
    
    for satir in metin.split('\n'):
        if not satir.strip():
            current_line += 1
            line_slope, line_offset = get_line_params(current_line)
            y_base = config['margin_top'] + (current_line * config['line_spacing']) - config['target_letter_height']
            if y_base + config['target_letter_height'] > max_y:
                sayfalar.append(sayfa); sayfa = create_page(); current_line = 0
                line_slope, line_offset = get_line_params(current_line)
                y_base = config['margin_top'] + (current_line * config['line_spacing']) - config['target_letter_height']
            x = config['margin_left']; continue
        
        for kelime in satir.split(' '):
            tahmini_w = len(kelime) * 50
            if x + tahmini_w > max_x and x > config['margin_left']:
                x = config['margin_left']; current_line += 1
                line_slope, line_offset = get_line_params(current_line)
                y_base = config['margin_top'] + (current_line * config['line_spacing']) - config['target_letter_height']
                if y_base + config['target_letter_height'] > max_y:
                    sayfalar.append(sayfa); sayfa = create_page(); current_line = 0
                    line_slope, line_offset = get_line_params(current_line)
                    y_base = config['margin_top'] + (current_line * config['line_spacing']) - config['target_letter_height']
            
            for harf in kelime:
                harf_resmi = harf_resmini_al(harfler, harf, config.get('murekkep_rengi'), config.get('opacity', 0.95), config.get('kalinlik', 0))
                if not harf_resmi: continue
                
                scale_noise = random.uniform(-0.01 * jitter, 0.01 * jitter)
                harf_resmi = harfi_boyutlandir(harf_resmi, int(config['target_letter_height'] * (1 + scale_noise)))
                angle = random.uniform(-0.2 * jitter, 0.2 * jitter)
                harf_resmi = harf_resmi.rotate(angle, resample=Image.BICUBIC, expand=True)
                
                gw, gh = harf_resmi.size
                if x + gw > max_x:
                    x = config['margin_left']; current_line += 1
                    line_slope, line_offset = get_line_params(current_line)
                    y_base = config['margin_top'] + (current_line * config['line_spacing']) - config['target_letter_height']
                    if y_base + config['target_letter_height'] > max_y:
                        sayfalar.append(sayfa); sayfa = create_page(); current_line = 0
                        line_slope, line_offset = get_line_params(current_line)
                        y_base = config['margin_top'] + (current_line * config['line_spacing']) - config['target_letter_height']
                
                slope_y = (x - config['margin_left']) * line_slope
                random_y = random.uniform(-jitter, jitter) * 0.5
                final_y = int(y_base + slope_y + line_offset + random_y - (gh - config['target_letter_height'])/2)
                sayfa.paste(harf_resmi, (x, final_y), harf_resmi)
                x += gw + random.randint(0, 4)
            x += config.get('word_spacing', 55)
        x = config['margin_left']; current_line += 1
        line_slope, line_offset = get_line_params(current_line)
        y_base = config['margin_top'] + (current_line * config['line_spacing']) - config['target_letter_height']
    
    sayfalar.append(sayfa)
    return sayfalar

def sayfalari_pdf_olustur(sayfalar):
    if not sayfalar: return None
    rgb_sayfalar = []
    for sayfa in sayfalar:
        rgb = Image.new('RGB', sayfa.size, (255, 255, 255))
        rgb.paste(sayfa, mask=sayfa.split()[3])
        rgb_sayfalar.append(rgb)
    buf = io.BytesIO()
    rgb_sayfalar[0].save(buf, 'PDF', resolution=300.0, save_all=True, append_images=rgb_sayfalar[1:], quality=95)
    buf.seek(0)
    return buf
