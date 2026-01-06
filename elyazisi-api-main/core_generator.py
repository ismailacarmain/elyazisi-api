#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from PIL import Image, ImageDraw
import os
import random
import io
import cv2
import numpy as np

KARAKTER_HARITASI = {
    # Küçük Harfler
    'a': 'kucuk_a', 'b': 'kucuk_b', 'c': 'kucuk_c', 'ç': 'kucuk_cc', 'd': 'kucuk_d', 'e': 'kucuk_e', 
    'f': 'kucuk_f', 'g': 'kucuk_g', 'ğ': 'kucuk_gg', 'h': 'kucuk_h', 'ı': 'kucuk_ii', 'i': 'kucuk_i', 
    'j': 'kucuk_j', 'k': 'kucuk_k', 'l': 'kucuk_l', 'm': 'kucuk_m', 'n': 'kucuk_n', 'o': 'kucuk_o', 
    'ö': 'kucuk_oo', 'p': 'kucuk_p', 'r': 'kucuk_r', 's': 'kucuk_s', 'ş': 'kucuk_ss', 't': 'kucuk_t', 
    'u': 'kucuk_u', 'ü': 'kucuk_uu', 'v': 'kucuk_v', 'y': 'kucuk_y', 'z': 'kucuk_z',
    'w': 'kucuk_w', 'q': 'kucuk_q', 'x': 'kucuk_x',
    # Büyük Harfler
    'A': 'buyuk_a', 'B': 'buyuk_b', 'C': 'buyuk_c', 'Ç': 'buyuk_cc', 'D': 'buyuk_d', 'E': 'buyuk_e', 
    'F': 'buyuk_f', 'G': 'buyuk_g', 'Ğ': 'buyuk_gg', 'H': 'buyuk_h', 'I': 'buyuk_ii', 'İ': 'buyuk_i', 
    'J': 'buyuk_j', 'K': 'buyuk_k', 'L': 'buyuk_l', 'M': 'buyuk_m', 'N': 'buyuk_n', 'O': 'buyuk_o', 
    'Ö': 'buyuk_oo', 'P': 'buyuk_p', 'R': 'buyuk_r', 'S': 'buyuk_s', 'Ş': 'buyuk_ss', 'T': 'buyuk_t', 
    'U': 'buyuk_u', 'Ü': 'buyuk_uu', 'V': 'buyuk_v', 'Y': 'buyuk_y', 'Z': 'buyuk_z',
    'W': 'buyuk_w', 'Q': 'buyuk_q', 'X': 'buyuk_x',
    # Rakamlar
    '0': 'rakam_0', '1': 'rakam_1', '2': 'rakam_2', '3': 'rakam_3', '4': 'rakam_4', 
    '5': 'rakam_5', '6': 'rakam_6', '7': 'rakam_7', '8': 'rakam_8', '9': 'rakam_9',
    # Özel Karakterler
    '.': 'ozel_nokta', ',': 'ozel_virgul', ':': 'ozel_ikiknokta', ';': 'ozel_noktalivirgul', 
    '?': 'ozel_soru', '!': 'ozel_unlem', '-': 'ozel_tire', '(': 'ozel_parantezac', 
    ')': 'ozel_parantezkapama', '"': 'ozel_tirnak', "'": 'ozel_tektirnak', '[': 'ozel_koseli_ac', 
    ']': 'ozel_koseli_kapa', '{': 'ozel_suslu_ac', '}': 'ozel_suslu_kapa', '/': 'ozel_slash', 
    '\': 'ozel_backslas', '|': 'ozel_pipe', '+': 'ozel_arti', '*': 'ozel_carpi', 
    '=': 'ozel_esit', '<': 'ozel_kucuktur', '>': 'ozel_buyuktur', '%': 'ozel_yuzde', 
    '^': 'ozel_sapka', '#': 'ozel_diyez', '~': 'ozel_yaklasik', '_': 'ozel_alt_tire',
    '@': 'ozel_at', '$': 'ozel_dolar', '€': 'ozel_euro', '₺': 'ozel_tl', '&': 'ozel_ampersand'
}

def karakter_anahtarini_bul(karakter):
    return KARAKTER_HARITASI.get(karakter)

def harf_resimlerini_yukle(klasor_yolu="static/harfler"):
    harfler = {}
    if not os.path.exists(klasor_yolu):
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
                except: pass
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
                    pixels[i, j] = (dither_r, dither_g, dither_b, int(a * opacity))
        if kalinlik != 0:
            img_array = np.array(harf_resmi)
            alpha = img_array[:, :, 3]
            kernel_size = abs(kalinlik) + 1
            kernel = np.ones((kernel_size, kernel_size), np.uint8)
            if kalinlik > 0: alpha = cv2.dilate(alpha, kernel, iterations=1) 
            else: alpha = cv2.erode(alpha, kernel, iterations=1)
            img_array[:, :, 3] = alpha
            harf_resmi = Image.fromarray(img_array)
        return harf_resmi
    return None

def harfi_boyutlandir(harf_resmi, hedef_yukseklik):
    orijinal_genislik, orijinal_yukseklik = harf_resmi.size
    if orijinal_yukseklik == 0: return harf_resmi
    oran = hedef_yukseklik / orijinal_yukseklik
    return harf_resmi.resize((int(orijinal_genislik * oran), int(hedef_yukseklik)), Image.Resampling.LANCZOS)

def cizgileri_ciz(sayfa, config):
    paper_type = config.get('paper_type', 'cizgili')
    if paper_type == 'duz': return sayfa
    draw = ImageDraw.Draw(sayfa)
    width, height = sayfa.size
    y = config['margin_top']
    while y < height - 100:
        draw.line([(0, y), (width, y)], fill=(135, 206, 250, 100), width=3)
        y += config['line_spacing']
    if paper_type == 'kareli':
        x = config['margin_left']
        while x < width:
            draw.line([(x, 0), (x, height)], fill=(135, 206, 250, 100), width=3)
            x += config['line_spacing']
    return sayfa

def yeni_sayfa_olustur(page_width, page_height, print_background, background_path=None):
    return Image.new("RGBA", (page_width, page_height), (255, 255, 255, 255))

def metni_sayfaya_yaz(metin, harfler, config):
    sayfalar = []
    jitter = config.get('jitter', 3)
    target_h = config['target_letter_height']
    
    descenders = "gjpyqğ_"
    smalls = "aceimnorsuvwxzçöüşiı-+*=< >%^#~"
    ascenders = "bdfhklt"
    punctuation = ".,:;'"
    tall_punctuation = "!?()[]{} /\|@$€₺&"

    def create_page():
        p = yeni_sayfa_olustur(config['page_width'], config['page_height'], False)
        return cizgileri_ciz(p, config)
    
    sayfa = create_page()
    current_line = 0
    x = config['margin_left']
    max_x = config['page_width'] - config['margin_right']
    max_y = config['page_height'] - 200
    
    for satir in metin.split('\n'):
        if not satir.strip():
            current_line += 1
            if config['margin_top'] + (current_line * config['line_spacing']) + target_h > max_y:
                sayfalar.append(sayfa); sayfa = create_page(); current_line = 0
            x = config['margin_left']; continue
        
        for kelime in satir.split(' '):
            tahmini_w = len(kelime) * (target_h * 0.6)
            if x + tahmini_w > max_x:
                x = config['margin_left']; current_line += 1
                if config['margin_top'] + (current_line * config['line_spacing']) + target_h > max_y:
                    sayfalar.append(sayfa); sayfa = create_page(); current_line = 0
            
            for harf in kelime:
                draw_scale = 1.0; baseline_shift = 0
                if harf in punctuation: draw_scale = 0.28
                elif harf in tall_punctuation: draw_scale = 0.90
                elif harf in descenders: draw_scale = 0.72; baseline_shift = target_h * 0.22
                elif harf in smalls: draw_scale = 0.72
                elif harf in ascenders: draw_scale = 0.95

                harf_resmi = harf_resmini_al(harfler, harf, config.get('murekkep_rengi'), config.get('opacity', 0.95), config.get('kalinlik', 0))
                if not harf_resmi: continue
                
                final_h = int(target_h * draw_scale * (1 + random.uniform(-0.01*jitter, 0.01*jitter)))
                harf_resmi = harfi_boyutlandir(harf_resmi, final_h)
                angle = random.uniform(-0.2 * jitter, 0.2 * jitter)
                harf_resmi = harf_resmi.rotate(angle, resample=Image.BICUBIC, expand=True)
                
                gw, gh = harf_resmi.size
                if x + gw > max_x:
                    x = config['margin_left']; current_line += 1
                    if config['margin_top'] + (current_line * config['line_spacing']) + target_h > max_y:
                        sayfalar.append(sayfa); sayfa = create_page(); current_line = 0
                
                y_base = config['margin_top'] + (current_line * config['line_spacing'])
                final_y = int(y_base + random.uniform(-jitter, jitter)*0.5 + baseline_shift - gh)
                sayfa.paste(harf_resmi, (x, final_y), harf_resmi)
                x += gw + random.randint(0, 4)
            x += config.get('word_spacing', 55)
        x = config['margin_left']; current_line += 1
    
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

class FormOlusturucu:
    def __init__(self):
        self.W, self.H = 2480, 3508
        self.GRID_W, self.GRID_H, self.CELL_SIZE = 10, 6, 150
        self.OFFSET_X = (self.W - (self.GRID_W * self.CELL_SIZE)) // 2
        self.OFFSET_Y = (self.H - (self.GRID_H * self.CELL_SIZE)) // 2
        
    def marker_olustur(self, marker_id, size=150):
        aruco_dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
        try: marker_img = cv2.aruco.generateImageMarker(aruco_dict, marker_id, size)
        except: marker_img = cv2.aruco.drawMarker(aruco_dict, marker_id, size)
        return Image.fromarray(marker_img).convert("RGBA")

    def form_sayfasi_olustur(self, page_id, characters, total_pages, example_images=None):
        img = Image.new("RGBA", (self.W, self.H), (255, 255, 255, 255))
        draw = ImageDraw.Draw(img)
        draw.text((self.W//2, 50), "FONTIFY - EL YAZISI TASARIM FORMU", fill=(100, 100, 100, 255), anchor="mm")
        
        m_ids = [page_id*4, page_id*4+1, page_id*4+2, page_id*4+3]
        positions = [(100, 100), (self.W-250, 100), (100, self.H-250), (self.W-250, self.H-250)]
        for mid, pos in zip(m_ids, positions): img.paste(self.marker_olustur(mid), pos)

        for r in range(self.GRID_H):
            for c in range(self.GRID_W):
                idx = r * self.GRID_W + c
                if idx >= len(characters): break
                char_info = characters[idx]
                x, y = self.OFFSET_X + c * self.CELL_SIZE, self.OFFSET_Y + r * self.CELL_SIZE
                draw.rectangle([x, y, x+self.CELL_SIZE, y+self.CELL_SIZE], outline=(220, 220, 220, 255), width=2)
                draw.text((x+8, y+5), char_info['label'], fill=(180, 180, 180, 255))
                if example_images and char_info['key'] in example_images:
                    ex = (example_images[char_info['key']][0] if isinstance(example_images[char_info['key']], list) else example_images[char_info['key']]).copy()
                    ex.thumbnail((self.CELL_SIZE-30, self.CELL_SIZE-30))
                    img.paste(ex, (x + (self.CELL_SIZE-ex.size[0])//2, y + (self.CELL_SIZE-ex.size[1])//2), ex)
        draw.text((self.W//2, self.H - 50), f"Sayfa {page_id + 1} / {total_pages}", fill=(150, 150, 150, 255), anchor="mm")
        return img

    def tum_formu_olustur(self, char_variation_count=3, is_example=False, local_assets=None):
        all_chars = []
        for char, key in KARAKTER_HARITASI.items():
            for i in range(1, char_variation_count + 1):
                all_chars.append({'key': f"{key}_{i}", 'label': f"{char} ({i})"})
        pages = []
        chars_per_page = self.GRID_W * self.GRID_H
        total_pages = (len(all_chars) + chars_per_page - 1) // chars_per_page
        for p in range(total_pages):
            start, end = p * chars_per_page, min((p+1) * chars_per_page, len(all_chars))
            pages.append(self.form_sayfasi_olustur(p, all_chars[start:end], total_pages, local_assets if is_example else None))
        return sayfalari_pdf_olustur(pages)