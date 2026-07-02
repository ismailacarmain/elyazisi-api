import cv2
import numpy as np
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import os

# ---------------------------------------------------------
# ORTAK VERİ YAPILARI
# ---------------------------------------------------------
def get_base_characters():
    lowers = "abcçdefgğhıijklmnoöpqrsştuüvwxyz"
    uppers = "ABCÇDEFGĞHIİJKLMNOÖPQRSŞTUÜVWXYZ"
    digits = "0123456789"
    symbols_str = ".,:;?!-_\"'()[]{}/\\|+*=< >%^~@$€₺&#"
    symbols_str = symbols_str.replace(" ", "")
    
    symbols = ""
    seen = set()
    for char in symbols_str:
        if char not in seen:
            symbols += char
            seen.add(char)
            
    return lowers, uppers, digits, symbols

def generate_marker(marker_id):
    aruco_dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    img = cv2.aruco.generateImageMarker(aruco_dict, marker_id, 200)
    path = f"m_{marker_id}.png"
    cv2.imwrite(path, img)
    return path

def create_form(filename="form.pdf", repetition=3, test_mode=False):
    try:
        pdfmetrics.registerFont(TTFont('Arial', "C:/Windows/Fonts/arial.ttf"))
        font = 'Arial'
    except:
        font = 'Helvetica'

    c = canvas.Canvas(filename, pagesize=A4)
    w, h = A4
    
    lowers, uppers, digits, symbols = get_base_characters()
    full_sequence = []
    
    for char in lowers: full_sequence.extend([char] * repetition)
    for char in uppers: full_sequence.extend([char] * repetition)
    for char in digits: full_sequence.extend([char] * repetition)
    for char in symbols: full_sequence.extend([char] * repetition)

    box_s = 15 * mm
    m_s = 15 * mm
    marg = 10 * mm
    
    boxes_per_page = 120
    num_pages = (len(full_sequence) + boxes_per_page - 1) // boxes_per_page
    
    char_idx = 0
    m_id = 0
    
    print(f"Oluşturuluyor: {filename}")
    
    for page in range(num_pages):
        # --- ÜST BİLGİ METNİ ---
        # "sayfanın üst tarafı bu taraf fontify.online"
        # Sayfanın en tepesine, küçük, siyah.
        c.saveState()
        c.setFont(font, 9) # Küçük punto
        c.setFillColorRGB(0, 0, 0) # Siyah
        header_text = "sayfanın üst tarafı bu taraf fontify.online"
        # Tam ortala
        c.drawCentredString(w/2, h - 8*mm, header_text)
        c.restoreState()

        sec_h = h / 2
        
        for sec in range(2): 
            y_off = h/2 if sec == 0 else 0
            
            # Markerlar
            for p in range(4):
                safe_m_id = m_id % 50 
                path = generate_marker(safe_m_id)
                
                if p==0: c.drawImage(path, marg, y_off+sec_h-marg-m_s, m_s, m_s)
                if p==1: c.drawImage(path, w-marg-m_s, y_off+sec_h-marg-m_s, m_s, m_s)
                if p==2: c.drawImage(path, marg, y_off+marg, m_s, m_s)
                if p==3: c.drawImage(path, w-marg-m_s, y_off+marg, m_s, m_s)
                
                os.remove(path)
                m_id += 1
            
            # Grid
            gx = (w - 10*box_s)/2
            gy = (sec_h - 6*box_s)/2
            
            for r in range(6):
                for col in range(10):
                    if char_idx < len(full_sequence):
                        cur_char = full_sequence[char_idx]
                        cur_x, cur_y = gx + col*box_s, y_off + gy + (5-r)*box_s
                        
                        c.setStrokeColorRGB(0,0,0)
                        c.rect(cur_x, cur_y, box_s, box_s)
                        
                        # --- KÜÇÜK REHBER YAZI KALDIRILDI ---
                        # c.drawString(...) SATIRI SİLİNDİ. 
                        
                        # Test modu (Dolu Form) - Büyük el yazısı simülasyonu
                        if test_mode:
                            c.saveState()
                            c.setFont(font, 24)
                            c.setFillColorRGB(0, 0, 0)
                            c.drawCentredString(cur_x + box_s/2, cur_y + box_s/2 - 3.5*mm, cur_char)
                            c.restoreState()
                            
                        char_idx += 1
        c.showPage()
    c.save()

if __name__ == "__main__":
    create_form("pdfler/bos/standart_form_3x.pdf", repetition=3, test_mode=False)