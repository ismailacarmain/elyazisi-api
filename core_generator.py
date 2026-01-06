#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from PIL import Image, ImageDraw, ImageFont
import os
import random
import io
import cv2
import numpy as np

# Karakter haritası - Unicode karakterler düzgün tanımlandı
KARAKTER_HARITASI = {
    'a': 'kucuk_a', 'b': 'kucuk_b', 'c': 'kucuk_c', 'ç': 'kucuk_cc', 'd': 'kucuk_d', 'e': 'kucuk_e', 
    'f': 'kucuk_f', 'g': 'kucuk_g', 'ğ': 'kucuk_gg', 'h': 'kucuk_h', 'ı': 'kucuk_ii', 'i': 'kucuk_i', 
    'j': 'kucuk_j', 'k': 'kucuk_k', 'l': 'kucuk_l', 'm': 'kucuk_m', 'n': 'kucuk_n', 'o': 'kucuk_o', 
    'ö': 'kucuk_oo', 'p': 'kucuk_p', 'r': 'kucuk_r', 's': 'kucuk_s', 'ş': 'kucuk_ss', 't': 'kucuk_t', 
    'u': 'kucuk_u', 'ü': 'kucuk_uu', 'v': 'kucuk_v', 'y': 'kucuk_y', 'z': 'kucuk_z',
    'w': 'kucuk_w', 'q': 'kucuk_q', 'x': 'kucuk_x',
    'A': 'buyuk_a', 'B': 'buyuk_b', 'C': 'buyuk_c', 'Ç': 'buyuk_cc', 'D': 'buyuk_d', 'E': 'buyuk_e', 
    'F': 'buyuk_f', 'G': 'buyuk_g', 'Ğ': 'buyuk_gg', 'H': 'buyuk_h', 'I': 'buyuk_ii', 'İ': 'buyuk_i', 
    'J': 'buyuk_j', 'K': 'buyuk_k', 'L': 'buyuk_l', 'M': 'buyuk_m', 'N': 'buyuk_n', 'O': 'buyuk_o', 
    'Ö': 'buyuk_oo', 'P': 'buyuk_p', 'R': 'buyuk_r', 'S': 'buyuk_s', 'Ş': 'buyuk_ss', 'T': 'buyuk_t', 
    'U': 'buyuk_u', 'Ü': 'buyuk_uu', 'V': 'buyuk_v', 'Y': 'buyuk_y', 'Z': 'buyuk_z',
    'W': 'buyuk_w', 'Q': 'buyuk_q', 'X': 'buyuk_x',
    '0': 'rakam_0', '1': 'rakam_1', '2': 'rakam_2', '3': 'rakam_3', '4': 'rakam_4', 
    '5': 'rakam_5', '6': 'rakam_6', '7': 'rakam_7', '8': 'rakam_8', '9': 'rakam_9',
    '.': 'ozel_nokta', ',': 'ozel_virgul', ':': 'ozel_ikiknokta', ';': 'ozel_noktalivirgul', 
    '?': 'ozel_soru', '!': 'ozel_unlem', '-': 'ozel_tire', '(': 'ozel_parantezac', 
    ')': 'ozel_parantezkapama', '"': 'ozel_tirnak', "'": 'ozel_tektirnak', '[': 'ozel_koseli_ac', 
    ']': 'ozel_koseli_kapa', '{': 'ozel_suslu_ac', '}': 'ozel_suslu_kapa', '/': 'ozel_slash', 
    '\\': 'ozel_backslash', '|': 'ozel_pipe', '+': 'ozel_arti', '*': 'ozel_carpi', 
    '=': 'ozel_esit', '<': 'ozel_kucuktur', '>': 'ozel_buyuktur', '%': 'ozel_yuzde', 
    '^': 'ozel_sapka', '#': 'ozel_diyez', '~': 'ozel_yaklasik', '_': 'ozel_alt_tire',
    '@': 'ozel_at', '$': 'ozel_dolar', '€': 'ozel_euro', '₺': 'ozel_tl', '&': 'ozel_ampersand'
}

def karakter_anahtarini_bul(karakter):
    """Karakterin anahtar ismini döndürür"""
    return KARAKTER_HARITASI.get(karakter)

def harf_resimlerini_yukle(klasor_yolu="static/harfler"):
    """Harf resimlerini klasörden yükler"""
    harfler = {}
    if not os.path.exists(klasor_yolu):
        return harfler
    
    for dosya in os.listdir(klasor_yolu):
        if dosya.endswith('.png'):
            parts = dosya.replace('.png', '').split('_')
            if len(parts) >= 2:
                anahtar = f"{parts[0]}_{parts[1]}"
                try:
                    resim = Image.open(os.path.join(klasor_yolu, dosya)).convert("RGBA")
                    if anahtar not in harfler:
                        harfler[anahtar] = []
                    harfler[anahtar].append(resim)
                except Exception as e:
                    print(f"Resim yüklenemedi: {dosya} - {e}")
    return harfler

def harf_resmini_al(harfler, karakter, murekkep_rengi=(27, 27, 29), opacity=0.95, kalinlik=0):
    """Karakterin resmini alır ve renklendirme yapar"""
    anahtar = karakter_anahtarini_bul(karakter)
    if anahtar and anahtar in harfler:
        harf_resmi = random.choice(harfler[anahtar]).copy()
        pixels = harf_resmi.load()
        
        # Renk değiştirme
        for i in range(harf_resmi.size[0]):
            for j in range(harf_resmi.size[1]):
                r, g, b, a = pixels[i, j]
                if r < 150 and g < 150 and b < 150 and a > 100:
                    pixels[i, j] = (murekkep_rengi[0], murekkep_rengi[1], murekkep_rengi[2], int(a * opacity))
        
        # Kalınlık ayarı
        if kalinlik > 0:
            img_array = np.array(harf_resmi)
            alpha = img_array[:, :, 3]
            kernel = np.ones((kalinlik + 1, kalinlik + 1), np.uint8)
            alpha = cv2.dilate(alpha, kernel, iterations=1)
            img_array[:, :, 3] = alpha
            harf_resmi = Image.fromarray(img_array)
        
        return harf_resmi
    return None

def harfi_boyutlandir(harf_resmi, hedef_yukseklik):
    """Harfi hedef yüksekliğe ölçeklendirir"""
    w, h = harf_resmi.size
    if h == 0:
        return harf_resmi
    oran = hedef_yukseklik / h
    return harf_resmi.resize((int(w * oran), int(hedef_yukseklik)), Image.Resampling.LANCZOS)

def cizgileri_ciz(sayfa, config):
    """Çizgili veya kareli kağıt çizer"""
    if config.get('paper_type') == 'duz':
        return sayfa
    
    draw = ImageDraw.Draw(sayfa)
    w, h = sayfa.size
    line_spacing = config['line_spacing']
    
    # Yatay çizgiler
    for y in range(config['margin_top'], h - 100, line_spacing):
        draw.line([(0, y), (w, y)], fill=(135, 206, 250, 100), width=3)
    
    # Kareli ise dikey çizgiler de ekle
    if config.get('paper_type') == 'kareli':
        for x in range(config['margin_left'], w, line_spacing):
            draw.line([(x, 0), (x, h)], fill=(135, 206, 250, 100), width=3)
    
    return sayfa

def metni_sayfaya_yaz(metin, harfler, config):
    """Metni el yazısı formatında sayfaya yazar"""
    sayfalar = []
    jitter = config.get('jitter', 3)
    target_h = config['target_letter_height']
    
    # Karakter grupları (JS motoruyla uyumlu)
    descenders = "gjpyqğ_"
    smalls = "aceimnorsuvwxzçöüşiı-+*=<>%^#~"
    ascenders = "bdfhklt"
    punctuation = ".,:;'\""
    tall_punctuation = "!?()[]{}/\\|@$€₺&"

    def create_page():
        """Yeni sayfa oluştur"""
        p = Image.new("RGBA", (config['page_width'], config['page_height']), (255, 255, 255, 255))
        return cizgileri_ciz(p, config)
    
    sayfa = create_page()
    curr_line = 0
    x = config['margin_left']
    max_x = config['page_width'] - config['margin_right']
    max_y = config['page_height'] - 200
    
    for satir in metin.split('\n'):
        if not satir.strip():
            # Boş satır
            curr_line += 1
            if config['margin_top'] + (curr_line * config['line_spacing']) > max_y:
                sayfalar.append(sayfa)
                sayfa = create_page()
                curr_line = 0
            x = config['margin_left']
            continue
        
        for kelime in satir.split(' '):
            # Kelime sığmıyorsa yeni satır
            if x + (len(kelime) * target_h * 0.6) > max_x:
                x = config['margin_left']
                curr_line += 1
                if config['margin_top'] + (curr_line * config['line_spacing']) > max_y:
                    sayfalar.append(sayfa)
                    sayfa = create_page()
                    curr_line = 0
            
            for harf in kelime:
                # Karakter tipine göre ölçeklendirme
                draw_scale = 1.0
                baseline_shift = 0
                
                if harf in punctuation:
                    draw_scale = 0.28
                elif harf in tall_punctuation:
                    draw_scale = 0.90
                elif harf in descenders:
                    draw_scale = 0.72
                    baseline_shift = target_h * 0.22
                elif harf in smalls:
                    draw_scale = 0.72
                elif harf in ascenders:
                    draw_scale = 0.95

                resim = harf_resmini_al(
                    harfler, 
                    harf, 
                    config.get('murekkep_rengi', (27, 27, 29)), 
                    config.get('opacity', 0.95), 
                    config.get('kalinlik', 0)
                )
                
                if not resim:
                    continue
                
                # Boyutlandırma ve hafif rastgelelik
                final_height = int(target_h * draw_scale * (1 + random.uniform(-0.01 * jitter, 0.01 * jitter)))
                resim = harfi_boyutlandir(resim, final_height)
                resim = resim.rotate(random.uniform(-0.2 * jitter, 0.2 * jitter), resample=Image.BICUBIC, expand=True)
                
                gw, gh = resim.size
                
                # Satır sonu kontrolü
                if x + gw > max_x:
                    x = config['margin_left']
                    curr_line += 1
                    if config['margin_top'] + (curr_line * config['line_spacing']) > max_y:
                        sayfalar.append(sayfa)
                        sayfa = create_page()
                        curr_line = 0
                
                # Harfi yapıştır
                y_pos = int(config['margin_top'] + (curr_line * config['line_spacing']) + 
                           random.uniform(-jitter, jitter) * 0.5 + baseline_shift - gh)
                sayfa.paste(resim, (x, y_pos), resim)
                x += gw + random.randint(0, 4)
            
            # Kelime arası boşluk
            x += config.get('word_spacing', 55)
        
        # Satır sonu
        x = config['margin_left']
        curr_line += 1
    
    sayfalar.append(sayfa)
    return sayfalar

def sayfalari_pdf_olustur(sayfalar):
    """RGBA sayfaları PDF'e dönüştürür"""
    if not sayfalar:
        return None
    
    rgb_list = []
    for s in sayfalar:
        rgb = Image.new('RGB', s.size, (255, 255, 255))
        rgb.paste(s, mask=s.split()[3])
        rgb_list.append(rgb)
    
    buf = io.BytesIO()
    rgb_list[0].save(
        buf, 
        'PDF', 
        resolution=300.0, 
        save_all=True, 
        append_images=rgb_list[1:], 
        quality=95
    )
    buf.seek(0)
    return buf

class FormOlusturucu:
    """Aruco markerli form sayfaları oluşturur"""
    
    def __init__(self):
        self.W = 2480
        self.H = 3508
        self.GRID_W = 10
        self.GRID_H = 6
        self.CELL_SIZE = 150
        self.OFFSET_X = (self.W - 1500) // 2
        self.OFFSET_Y = (self.H - 900) // 2 + 100
        
    def marker_olustur(self, marker_id, size=200):
        """Aruco marker oluşturur"""
        aruco_dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
        try:
            # Yeni OpenCV API
            marker_img = cv2.aruco.generateImageMarker(aruco_dict, marker_id, size)
        except AttributeError:
            # Eski OpenCV API
            marker_img = cv2.aruco.drawMarker(aruco_dict, marker_id, size)
        
        return Image.fromarray(marker_img).convert("RGBA")

    def form_sayfasi_olustur(self, page_id, characters, total_pages, example_images=None):
        """Tek bir form sayfası oluşturur"""
        img = Image.new("RGBA", (self.W, self.H), (255, 255, 255, 255))
        draw = ImageDraw.Draw(img)
        
        # Başlık
        draw.text((self.W // 2, 80), "FONTIFY - TASARIM FORMU", 
                 fill=(100, 100, 100, 255), anchor="mm")
        
        # 4 köşeye Aruco markerlar
        positions = [(50, 50), (self.W - 250, 50), (50, self.H - 250), (self.W - 250, self.H - 250)]
        for i, pos in enumerate(positions):
            img.paste(self.marker_olustur(page_id * 4 + i), pos)

        # Karakter kutucukları
        for i, char in enumerate(characters):
            x = self.OFFSET_X + (i % 10) * self.CELL_SIZE
            y = self.OFFSET_Y + (i // 10) * self.CELL_SIZE
            
            # Kutucuk çiz
            draw.rectangle(
                [x, y, x + self.CELL_SIZE, y + self.CELL_SIZE], 
                outline=(220, 220, 220, 255), 
                width=2
            )
            
            # Karakter etiketi
            draw.text((x + 8, y + 5), char['label'], fill=(180, 180, 180, 255))
            
            # Örnek resim varsa ekle
            if example_images and char['key'] in example_images:
                ex_img = example_images[char['key']]
                if isinstance(ex_img, list):
                    ex_img = ex_img[0]
                ex_img = ex_img.copy()
                ex_img.thumbnail((self.CELL_SIZE - 30, self.CELL_SIZE - 30))
                paste_x = x + (self.CELL_SIZE - ex_img.size[0]) // 2
                paste_y = y + (self.CELL_SIZE - ex_img.size[1]) // 2
                img.paste(ex_img, (paste_x, paste_y), ex_img)
        
        # Sayfa numarası
        draw.text((self.W // 2, self.H - 50), 
                 f"Sayfa {page_id + 1} / {total_pages}", 
                 fill=(150, 150, 150, 255), anchor="mm")
        
        return img

    def tum_formu_olustur(self, char_variation_count=3, is_example=False, local_assets=None):
        """Tüm form sayfalarını oluşturup PDF'e dönüştürür"""
        all_chars = []
        
        # Tüm karakterler için varyasyonlar oluştur
        for char, key in KARAKTER_HARITASI.items():
            for i in range(1, char_variation_count + 1):
                all_chars.append({
                    'key': f"{key}_{i}", 
                    'label': f"{char} ({i})"
                })
        
        # Sayfaları oluştur (her sayfa 60 karakter)
        pages = []
        total_pages = (len(all_chars) + 59) // 60
        
        for p in range(total_pages):
            start_idx = p * 60
            end_idx = min((p + 1) * 60, len(all_chars))
            page_chars = all_chars[start_idx:end_idx]
            
            pages.append(
                self.form_sayfasi_olustur(
                    p, 
                    page_chars, 
                    total_pages, 
                    local_assets if is_example else None
                )
            )
        
        return sayfalari_pdf_olustur(pages)
