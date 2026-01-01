from flask import Flask, request, jsonify
import cv2
import numpy as np
from PIL import Image
import os
import base64
import io
import firebase_admin
from firebase_admin import credentials, firestore
import uuid

app = Flask(__name__)

# Firebase başlat (Storage YOK - sadece Firestore)
if not firebase_admin._apps:
    cred_dict = {
        "type": "service_account",
        "project_id": os.environ.get("FIREBASE_PROJECT_ID"),
        "private_key_id": os.environ.get("FIREBASE_PRIVATE_KEY_ID"),
        "private_key": os.environ.get("FIREBASE_PRIVATE_KEY", "").replace("\\n", "\n"),
        "client_email": os.environ.get("FIREBASE_CLIENT_EMAIL"),
        "client_id": os.environ.get("FIREBASE_CLIENT_ID"),
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    cred = credentials.Certificate(cred_dict)
    firebase_admin.initialize_app(cred)

db = firestore.client()


class HarfTarayici:
    def __init__(self):
        self.kucuk_harfler = "abcçdefgğhıijklmnoöprsştuüvyz"
        self.buyuk_harfler = "ABCÇDEFGĞHIİJKLMNOÖPRSŞTUÜVYZ"
        self.rakamlar = "0123456789"
        self.noktalama = ".,:;?!-()"
        self.varyasyon_sayisi = 3
        self.satir_toleransi = 20
        self.sutun_toleransi = 20

    def karakter_listesi_olustur(self):
        liste = []
        for h in self.kucuk_harfler:
            for v in range(1, self.varyasyon_sayisi + 1):
                liste.append(("kucuk", h, v))
        for h in self.buyuk_harfler:
            for v in range(1, self.varyasyon_sayisi + 1):
                liste.append(("buyuk", h, v))
        for h in self.rakamlar:
            for v in range(1, self.varyasyon_sayisi + 1):
                liste.append(("rakam", h, v))
        
        map_ozel = {".": "nokta", ",": "virgul", ":": "ikiknokta", 
                   ";": "noktalivirgul", "?": "soru", "!": "unlem", "-": "tire", 
                   "(": "parantezac", ")": "parantezkapama"}
        for h in self.noktalama:
            ad = map_ozel.get(h, h)
            for v in range(1, self.varyasyon_sayisi + 1):
                liste.append(("ozel", ad, v))
        return liste

    def kirmizi_kutulari_bul(self, goruntu):
        hsv = cv2.cvtColor(goruntu, cv2.COLOR_BGR2HSV)
        
        lower1 = np.array([0, 100, 100])
        upper1 = np.array([10, 255, 255])
        lower2 = np.array([160, 100, 100])
        upper2 = np.array([180, 255, 255])
        
        mask = cv2.bitwise_or(cv2.inRange(hsv, lower1, upper1), cv2.inRange(hsv, lower2, upper2))
        
        kernel = np.ones((2,2), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
        
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        kutular = []
        
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < 500:
                continue
            
            x, y, w, h = cv2.boundingRect(cnt)
            aspect_ratio = float(w)/h
            
            if 0.7 < aspect_ratio < 1.3:
                kutular.append((x, y, w, h))
        
        return kutular

    def grid_olustur_ve_sirala(self, kutular):
        if not kutular:
            return []
        
        y_coords = sorted([k[1] for k in kutular])
        rows = []
        if y_coords:
            current = [y_coords[0]]
            for y in y_coords[1:]:
                if y - np.mean(current) < self.satir_toleransi:
                    current.append(y)
                else:
                    rows.append(int(np.mean(current)))
                    current = [y]
            rows.append(int(np.mean(current)))
            
        x_coords = sorted([k[0] for k in kutular])
        cols = []
        if x_coords:
            current = [x_coords[0]]
            for x in x_coords[1:]:
                if x - np.mean(current) < self.sutun_toleransi:
                    current.append(x)
                else:
                    cols.append(int(np.mean(current)))
                    current = [x]
            cols.append(int(np.mean(current)))
        
        res = []
        for ry in rows:
            for cx in cols:
                found = None
                best_dist = 999
                for k in kutular:
                    dist = abs(k[0]-cx) + abs(k[1]-ry)
                    if abs(k[0]-cx) < self.sutun_toleransi and abs(k[1]-ry) < self.satir_toleransi:
                        if dist < best_dist:
                            best_dist = dist
                            found = k
                res.append(found)
        return res

    def kutuyu_isle(self, goruntu, kutu):
        if kutu is None:
            return None
        x, y, w, h = kutu
        m = 12
        x1, y1, x2, y2 = x+m, y+m, x+w-m, y+h-m
        
        crop = goruntu[y1:y2, x1:x2]
        if crop.size == 0:
            return None
        
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        m_red = cv2.bitwise_or(
            cv2.inRange(hsv, np.array([0, 50, 50]), np.array([20, 255, 255])),
            cv2.inRange(hsv, np.array([150, 50, 50]), np.array([180, 255, 255]))
        )
        crop[m_red > 0] = [255, 255, 255]
        
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        gray = cv2.convertScaleAbs(gray, alpha=1.5, beta=-30)
        
        binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, 
                                      cv2.THRESH_BINARY_INV, 21, 5)
        
        coords = cv2.findNonZero(binary)
        if coords is None:
            return None
        
        xi, yi, wi, hi = cv2.boundingRect(coords)
        xf = max(0, xi - 2)
        wf = min(crop.shape[1] - xi + 2, wi + 4)
        f_bin = binary[:, xf:xf+wf]
        
        pil_img = Image.fromarray(f_bin).convert("L")
        rgba = Image.new("RGBA", pil_img.size)
        
        data = [(0,0,0, min(255, p*5 + 150)) if p > 0 else (255,255,255,0) for p in pil_img.getdata()]
        rgba.putdata(data)
        return rgba

    def process_images(self, image1_bytes, image2_bytes):
        """İki sayfa JPG'yi işle ve harf listesi döndür"""
        results = []
        chars = self.karakter_listesi_olustur()
        c_idx = 0
        
        for img_bytes in [image1_bytes, image2_bytes]:
            if img_bytes is None:
                continue
                
            nparr = np.frombuffer(img_bytes, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            
            if img is None:
                continue
            
            kutular = self.kirmizi_kutulari_bul(img)
            ks = self.grid_olustur_ve_sirala(kutular)
            
            for k in ks:
                if c_idx >= len(chars):
                    break
                tip, harf, var = chars[c_idx]
                
                if k is not None:
                    res = self.kutuyu_isle(img, k)
                    if res:
                        # PNG'yi base64'e çevir
                        buffer = io.BytesIO()
                        res.save(buffer, format='PNG')
                        png_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
                        
                        results.append({
                            'tip': tip,
                            'harf': harf,
                            'varyasyon': var,
                            'png_base64': png_base64
                        })
                c_idx += 1
        
        return results


tarayici = HarfTarayici()


@app.route('/')
def home():
    return jsonify({
        'status': 'ok',
        'message': 'El Yazısı API çalışıyor!',
        'endpoints': {
            '/process': 'POST - İki JPG gönder, harfleri al',
            '/health': 'GET - Sunucu durumu'
        }
    })


@app.route('/health')
def health():
    return jsonify({'status': 'healthy'})


@app.route('/process', methods=['POST'])
def process_form():
    """
    İki sayfa JPG al, harfleri çıkar, Firebase Firestore'a kaydet
    
    Body (JSON):
    {
        "user_id": "firebase_user_id",
        "font_name": "El Yazım 1",
        "image1": "base64_encoded_jpg",
        "image2": "base64_encoded_jpg"
    }
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({'error': 'JSON body gerekli'}), 400
        
        user_id = data.get('user_id')
        font_name = data.get('font_name', 'El Yazım')
        image1_b64 = data.get('image1')
        image2_b64 = data.get('image2')
        
        if not user_id:
            return jsonify({'error': 'user_id gerekli'}), 400
        
        if not image1_b64 or not image2_b64:
            return jsonify({'error': 'Her iki sayfa da gerekli (image1, image2)'}), 400
        
        # Base64'ten bytes'a çevir
        image1_bytes = base64.b64decode(image1_b64)
        image2_bytes = base64.b64decode(image2_b64)
        
        # Harfleri işle
        harfler = tarayici.process_images(image1_bytes, image2_bytes)
        
        if not harfler:
            return jsonify({'error': 'Hiç harf tespit edilemedi. Kırmızı kutulu form kullanın.'}), 400
        
        # Font ID oluştur
        font_id = str(uuid.uuid4())
        
        # Harfleri base64 olarak dict'e topla (Storage YOK - direkt Firestore'a)
        harf_data_dict = {}
        for harf_data in harfler:
            key = f"{harf_data['tip']}_{harf_data['harf']}_{harf_data['varyasyon']}"
            harf_data_dict[key] = harf_data['png_base64']
        
        # Firestore'a font bilgisini kaydet
        font_doc = {
            'font_id': font_id,
            'font_name': font_name,
            'user_id': user_id,
            'harf_sayisi': len(harfler),
            'harfler': harf_data_dict,
            'created_at': firestore.SERVER_TIMESTAMP,
            'status': 'completed'
        }
        
        db.collection('users').document(user_id).collection('fonts').document(font_id).set(font_doc)
        
        return jsonify({
            'success': True,
            'font_id': font_id,
            'font_name': font_name,
            'harf_sayisi': len(harfler),
            'message': f'{len(harfler)} harf başarıyla işlendi ve Firestore\'a kaydedildi!'
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
