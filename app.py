from flask import Flask, request, jsonify
from flask_cors import CORS
import cv2
import numpy as np
import os
import base64
import firebase_admin
from firebase_admin import credentials, firestore
import json
import traceback

app = Flask(__name__)
CORS(app)

# --- FIREBASE BAĞLANTISI (GÜVENLİ MOD) ---
# Global 'db' değişkenini başta None yapıyoruz.
# Böylece bağlantı başarısız olsa bile uygulama açılabilir.
db = None

def init_firebase():
    global db
    if db is not None:
        return db

    try:
        if not firebase_admin._apps:
            cred = None
            # 1. Environment Variable Kontrolü
            if os.environ.get('FIREBASE_CREDENTIALS'):
                try:
                    cred_dict = json.loads(os.environ.get('FIREBASE_CREDENTIALS'))
                    cred = credentials.Certificate(cred_dict)
                    print("Firebase: Env Var okundu.")
                except Exception as e:
                    print(f"Firebase Env Hatası: {e}")

            # 2. Dosya Kontrolü (Local Test İçin)
            if not cred and os.path.exists('serviceAccountKey.json'):
                try:
                    cred = credentials.Certificate('serviceAccountKey.json')
                    print("Firebase: Dosya okundu.")
                except Exception as e:
                    print(f"Firebase Dosya Hatası: {e}")

            # 3. Başlatma
            if cred:
                firebase_admin.initialize_app(cred)
            else:
                # Render bazen default creds ile çalışabilir, deneyelim
                firebase_admin.initialize_app()
                print("Firebase: Default creds denendi.")
        
        db = firestore.client()
        print("Firestore bağlantısı BAŞARILI.")
    except Exception as e:
        print(f"KRİTİK FIREBASE HATASI (Uygulama çalışmaya devam edecek): {e}")
        db = None # Bağlanamazsa None kalsın, uygulama çökmesin
    
    return db

# Uygulama açılırken bir kere denesin ama hatayı yutsun
init_firebase()

class HarfSistemi:
    def __init__(self):
        self.char_list = []
        # Çalışan Dosya Mantığı (kucuk_, buyuk_)
        low = "abcçdefgğhıijklmnoöprsştuüvyz"
        upp = "ABCÇDEFGĞHIİJKLMNOÖPRSŞTUÜVYZ"
        num = "0123456789"
        punc = {".":"nokta", ",":"virgul", ":":"ikiknokta", ";":"noktalivirgul", "?":"soru", "!":"unlem", "-":"tire", "(":"parantezac", ")":"parantezkapama"}
        
        for c in low: [self.char_list.append(f"kucuk_{c}_{i}") for i in range(1,4)]
        for c in upp: [self.char_list.append(f"buyuk_{c}_{i}") for i in range(1,4)]
        for c in num: [self.char_list.append(f"rakam_{c}_{i}") for i in range(1,4)]
        for c, n in punc.items(): [self.char_list.append(f"ozel_{n}_{i}") for i in range(1,4)]

    def crop_tight(self, binary_img):
        coords = cv2.findNonZero(binary_img)
        if coords is None: return None
        x, y, w, h = cv2.boundingRect(coords)
        return binary_img[y:y+h, x:x+w]

    def process_roi(self, roi):
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (3,3), 0)
        thresh = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 25, 12)
        
        kernel = np.ones((2,2), np.uint8)
        thresh = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel)
        
        tight = self.crop_tight(thresh)
        if tight is None: return None
        
        h, w = tight.shape
        rgba = np.zeros((h, w, 4), dtype=np.uint8)
        rgba[:,:,3] = tight 
        return rgba

    def detect_markers(self, img):
        try:
            # ROBUST DETECT MARKERS (0 Harf Hatası Fixi)
            aruco_dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
            parameters = cv2.aruco.DetectorParameters()
            # Hassas ayarlar
            parameters.adaptiveThreshWinSizeMin = 3
            parameters.adaptiveThreshWinSizeMax = 23
            parameters.adaptiveThreshWinSizeStep = 5
            parameters.minMarkerPerimeterRate = 0.01

            detector = cv2.aruco.ArucoDetector(aruco_dict, parameters)
            
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8,8))
            
            # 4 Farklı Yöntem (Karanlık, Aydınlık, Kontrastlı)
            variations = [
                gray,                                                                   
                clahe.apply(gray),                                                      
                cv2.threshold(gray, 128, 255, cv2.THRESH_BINARY)[1],                    
                cv2.threshold(clahe.apply(gray), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
            ]
            
            best_corners, best_ids = None, None
            
            for v in variations:
                corners, ids, _ = detector.detectMarkers(v)
                if ids is not None:
                    if best_ids is None or len(ids) > len(best_ids):
                        best_corners, best_ids = corners, ids
                    if len(ids) >= 4:
                        break
            
            return best_corners, best_ids
        except Exception as e:
            print(f"Marker Error: {e}")
            return None, None

    def process_single_page(self, img, section_id):
        corners, ids = self.detect_markers(img)
        # Hata mesajını detaylandıralım
        if ids is None or len(ids) < 4:
            found = len(ids) if ids is not None else 0
            return None, f"Markerlar bulunamadı! (Bulunan: {found}/4). Işık yansımasını kontrol edin."
            
        ids = ids.flatten()
        base = int(min(ids))
        bid = int(base // 4)
        
        scale = 10
        sw, sh = 210 * scale, 148 * scale
        m = 175
        
        targets = [bid*4, bid*4+1, bid*4+2, bid*4+3]
        src_points = []
        for target in targets:
            found = False
            for i in range(len(ids)):
                if int(ids[i]) == target:
                    src_points.append(np.mean(corners[i][0], axis=0))
                    found = True
                    break
            if not found: return None, f"Marker {target} eksik. Formun o köşesi çıkmamış."

        src = np.float32(src_points)
        dst = np.float32([[m,m], [sw-m,m], [m,sh-m], [sw-m,sh-m]])
        warped = cv2.warpPerspective(img, cv2.getPerspectiveTransform(src, dst), (sw, sh))
        
        b_px = 150
        sx, sy = int((sw - 1500)/2), int((sh - 900)/2)
        start_idx = bid * 60
        
        page_results = {}
        detected_count = 0
        missing_chars = []
        
        for r in range(6):
            for c in range(10):
                char_idx = start_idx + (r * 10 + c)
                if char_idx >= len(self.char_list): break
                
                char_name = self.char_list[char_idx]
                p = 15
                roi = warped[sy+r*b_px+p : sy+r*b_px+b_px-p, sx+c*b_px+p : sx+c*b_px+b_px-p]
                res = self.process_roi(roi)
                
                if res is not None:
                    # Gürültü filtresi: Çok küçükse (örn. nokta kadar) alma
                    if np.count_nonzero(res[:,:,3]) > 10: 
                        _, buffer = cv2.imencode(".png", res)
                        png_base64 = base64.b64encode(buffer).decode('utf-8')
                        page_results[char_name] = png_base64
                        detected_count += 1
                    else:
                        missing_chars.append(char_name)
                else:
                    missing_chars.append(char_name)
                    
        return {
            'harfler': page_results,
            'detected': detected_count,
            'total': min(60, len(self.char_list) - start_idx),
            'missing': missing_chars,
            'section_id': bid
        }, None

sistem = HarfSistemi()

@app.route('/')
def home():
    # Firebase durumunu da göster
    status = "Bağlı" if db else "Bağlı Değil (Ama Çalışıyor)"
    return jsonify({'status': 'ok', 'engine': 'aruco_v8_crash_proof', 'db_status': status})

@app.route('/process_single', methods=['POST'])
def process_single():
    try:
        data = request.get_json()
        user_id = data.get('user_id')
        font_name = data.get('font_name')
        section_id_param = data.get('section_id', 0)
        img_b64 = data.get('image_base64')

        if not user_id or not img_b64:
            return jsonify({'success': False, 'message': 'Eksik veri'}), 400

        nparr = np.frombuffer(base64.b64decode(img_b64), np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        result, error = sistem.process_single_page(img, section_id_param)
        if error:
            return jsonify({'success': False, 'message': error}), 400

        # --- FIREBASE KAYIT (Hata Yakalamalı) ---
        try:
            # Bağlantı yoksa tekrar dene (Lazy Init)
            database = init_firebase()
            
            if database:
                font_doc_id = f"{user_id}_{font_name.replace(' ', '_')}"
                doc_ref = database.collection('fonts').document(font_doc_id)
                user_font_ref = database.collection('users').document(user_id).collection('fonts').document(font_doc_id)
                
                doc = doc_ref.get()
                if not doc.exists:
                    payload = {
                        'owner_id': user_id,
                        'user_id': user_id,
                        'font_name': font_name,
                        'harfler': result['harfler'],
                        'harf_sayisi': len(result['harfler']),
                        'created_at': firestore.SERVER_TIMESTAMP,
                        'sections_completed': [result['section_id']]
                    }
                    doc_ref.set(payload)
                    user_font_ref.set(payload)
                else:
                    curr = doc.to_dict()
                    new_harfler = curr.get('harfler', {})
                    new_harfler.update(result['harfler'])
                    
                    completed = curr.get('sections_completed', [])
                    if result['section_id'] not in completed:
                        completed.append(result['section_id'])
                    
                    payload = {
                        'harfler': new_harfler,
                        'harf_sayisi': len(new_harfler),
                        'sections_completed': completed
                    }
                    doc_ref.update(payload)
                    user_font_ref.update(payload)
            else:
                print("Uyarı: DB bağlantısı olmadığı için kayıt atlandı.")

        except Exception as db_err:
            print(f"DB Kayıt Hatası: {db_err}")
            # Hata olsa bile devam et, kullanıcıya harfleri dön

        return jsonify({
            'success': True,
            'section_id': result['section_id'],
            'detected_chars': result['detected'],
            'total_chars': result['total'],
            'missing_chars': result['missing']
        })

    except Exception as e:
        print(f"Sunucu Hatası: {e}")
        traceback.print_exc()
        return jsonify({'success': False, 'message': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
