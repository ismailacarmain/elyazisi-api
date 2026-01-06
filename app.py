# -*- coding: utf-8 -*-
from flask import Flask, request, jsonify, render_template, send_file
from flask_cors import CORS
import cv2
import numpy as np
import os
import base64
import firebase_admin
from firebase_admin import credentials, firestore
import json
import traceback
import io
import core_generator as core_generator

app = Flask(__name__, template_folder='templates', static_folder='static')
CORS(app)

# --- FIREBASE BAĞLANTISI ---
db = None
connected_project_id = "BILINMIYOR"
init_error = None

def init_firebase():
    global db, init_error, connected_project_id
    if db is not None: return db
    try:
        cred = None
        env_creds = os.environ.get('FIREBASE_CREDENTIALS')
        if env_creds:
            cred_dict = json.loads(env_creds.strip())
            cred = credentials.Certificate(cred_dict)
            connected_project_id = cred_dict.get('project_id', 'EnvJson')
        
        if not cred and os.environ.get('FIREBASE_PRIVATE_KEY'):
            try:
                private_key = os.environ.get('FIREBASE_PRIVATE_KEY', "").replace('\\n', '\n')
                cred_dict = {
                    "type": "service_account",
                    "project_id": os.environ.get('FIREBASE_PROJECT_ID'),
                    "private_key_id": os.environ.get('FIREBASE_PRIVATE_KEY_ID'),
                    "private_key": private_key,
                    "client_email": os.environ.get('FIREBASE_CLIENT_EMAIL'),
                    "client_id": os.environ.get('FIREBASE_CLIENT_ID'),
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
                cred = credentials.Certificate(cred_dict)
                connected_project_id = cred_dict.get('project_id')
            except Exception as e: init_error = f"Env Vars Hatası: {e}"

        if not cred:
            paths = ['serviceAccountKey.json', '/etc/secrets/serviceAccountKey.json']
            for p in paths:
                if os.path.exists(p):
                    cred = credentials.Certificate(p)
                    with open(p, 'r') as f: connected_project_id = json.load(f).get('project_id', 'Dosya')
                    break
        
        if cred:
            if not firebase_admin._apps: firebase_admin.initialize_app(cred)
            db = firestore.client()
            print(f"✅ Firestore BAĞLANDI: {connected_project_id}")
        else:
            print("⚠️  UYARI: Firebase credentials bulunamadı.")
    except Exception as e:
        init_error = str(e)
        db = None
        print(f"❌ Firebase Hatası: {e}")
    return db

init_firebase()

# --- YENİ HARF SİSTEMİ (107 KARAKTER) ---
class HarfSistemi:
    def __init__(self):
        self.char_list = []
        
        # 1. Küçük harfler (İngilizce + Türkçe)
        lowercase_en = "abcdefghijklmnopqrstuvwxyz"
        lowercase_tr = "çğıöşü"
        
        # 2. Büyük harfler (İngilizce + Türkçe)
        uppercase_en = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        uppercase_tr = "ÇĞİÖŞÜ"
        
        # 3. Rakamlar
        numbers = "0123456789"
        
        # 4. Noktalama
        punctuation = {
            '.': 'nokta', ',': 'virgul', ':': 'ikiknokta', ';': 'noktalivirgul',
            '?': 'soru', '!': 'unlem', '-': 'tire', '_': 'alt_tire', 
            '"': 'tirnak', "'": 'tektirnak',
            '(': 'parantezac', ')': 'parantezkapama',
            '[': 'koseli_ac', ']': 'koseli_kapa',
            '{': 'suslu_ac', '}': 'suslu_kapa',
            '/': 'slash', '\\': 'backslash', '|': 'pipe'
        }
        
        # 5. Matematiksel
        math_chars = {
            '+': 'arti', '*': 'carpi', '=': 'esit', 
            '<': 'kucuktur', '>': 'buyuktur',
            '%': 'yuzde', '^': 'sapka', '~': 'yaklasik'
        }
        
        # 6. Sosyal medya
        social_chars = {
            '@': 'at', '$': 'dolar', '€': 'euro', 
            '₺': 'tl', '&': 'ampersand', '#': 'diyez'
        }
        
        # Listeyi oluştur
        for c in lowercase_en: [self.char_list.append(f"kucuk_{c}_{i}") for i in range(1, 4)]
        for c in lowercase_tr: [self.char_list.append(f"kucuk_{c}_{i}") for i in range(1, 4)]
        for c in uppercase_en: [self.char_list.append(f"buyuk_{c}_{i}") for i in range(1, 4)]
        for c in uppercase_tr: [self.char_list.append(f"buyuk_{c}_{i}") for i in range(1, 4)]
        for c in numbers: [self.char_list.append(f"rakam_{c}_{i}") for i in range(1, 4)]
        for c, n in punctuation.items(): [self.char_list.append(f"ozel_{n}_{i}") for i in range(1, 4)]
        for c, n in math_chars.items(): [self.char_list.append(f"ozel_{n}_{i}") for i in range(1, 4)]
        for c, n in social_chars.items(): [self.char_list.append(f"ozel_{n}_{i}") for i in range(1, 4)]
        
        print(f"📝 Sistem başlatıldı: {len(self.char_list)} karakter tanımlı")

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
        image_with_bg = np.full((h, w, 3), 255, dtype=np.uint8)
        image_with_bg[tight == 255] = [0, 0, 0]
        return image_with_bg

    def detect_markers(self, img):
        try:
            aruco_dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
            parameters = cv2.aruco.DetectorParameters()
            parameters.adaptiveThreshWinSizeMin = 3
            parameters.adaptiveThreshWinSizeMax = 23
            parameters.adaptiveThreshWinSizeStep = 5
            parameters.minMarkerPerimeterRate = 0.01
            detector = cv2.aruco.ArucoDetector(aruco_dict, parameters)
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8,8))
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
                    if len(ids) >= 4: break
            return best_corners, best_ids
        except:
            return None, None

    def process_section(self, img, section_id):
        """Tek bölüm işle (40 karakter - 8x5 grid)"""
        corners, ids = self.detect_markers(img)
        if ids is None or len(ids) < 4:
            return None, f"Markerlar bulunamadı ({len(ids) if ids else 0}/4)."
        
        ids = ids.flatten()
        base = int(min(ids))
        bid = int(base // 4)
        
        # A4 boyut (2480x3508) -> 8x5 grid = 40 karakter
        scale = 10
        sw, sh = 248 * scale, 350 * scale
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
            if not found:
                return None, f"Marker {target} eksik."
        
        src = np.float32(src_points)
        dst = np.float32([[m,m], [sw-m,m], [m,sh-m], [sw-m,sh-m]])
        warped = cv2.warpPerspective(img, cv2.getPerspectiveTransform(src, dst), (sw, sh))
        
        # Grid ayarları (8x5 = 40 karakter)
        CELL_SIZE = 200
        GRID_COLS = 8
        GRID_ROWS = 5
        GRID_WIDTH = GRID_COLS * CELL_SIZE
        OFFSET_X = (sw - GRID_WIDTH) // 2
        OFFSET_Y = 250  # Sabit, tek bölüm işliyoruz
        
        start_idx = bid * 40
        section_results = {}
        detected_count = 0
        missing_chars = []
        
        for r in range(GRID_ROWS):
            for c in range(GRID_COLS):
                idx = start_idx + (r * GRID_COLS + c)
                if idx >= len(self.char_list): break
                
                x = OFFSET_X + c * CELL_SIZE
                y = OFFSET_Y + r * CELL_SIZE
                
                # Padding
                p = 15
                roi = warped[y+p:y+CELL_SIZE-p, x+p:x+CELL_SIZE-p]
                
                res = self.process_roi(roi)
                if res is not None:
                    _, buffer = cv2.imencode(".png", res)
                    b64_str = base64.b64encode(buffer).decode('utf-8').replace('\n', '')
                    section_results[self.char_list[idx]] = b64_str
                    detected_count += 1
                else:
                    missing_chars.append(self.char_list[idx])
        
        return {
            'harfler': section_results,
            'detected': detected_count,
            'total': min(40, len(self.char_list) - start_idx),
            'missing': missing_chars,
            'section_id': bid
        }, None

sistem = HarfSistemi()

# --- HEALTH CHECK ---
@app.route('/health')
def health():
    return "OK", 200

# --- WEB ROTALARI ---
@app.route('/')
def index():
    return jsonify({
        "status": "running",
        "service": "Fontify API",
        "character_count": len(sistem.char_list),
        "firebase": connected_project_id
    })

@app.route('/api/list_fonts')
def list_fonts():
    user_id = request.args.get('user_id')
    fonts = []
    database = init_firebase()
    if not database: return jsonify({"success": False, "error": "Veritabanı bağlantısı yok"})
    try:
        public_fonts = database.collection('fonts').stream()
        for doc in public_fonts:
            d = doc.to_dict()
            f_name = d.get('font_name') or d.get('font_id') or doc.id
            fonts.append({'id': d.get('font_id', doc.id), 'name': f_name, 'type': 'public'})
        if user_id:
            private_fonts = database.collection('users').document(user_id).collection('fonts').stream()
            for doc in private_fonts:
                d = doc.to_dict()
                fid = d.get('font_id', doc.id)
                f_name = d.get('font_name') or fid
                if not any(f['id'] == fid for f in fonts):
                    fonts.append({'id': fid, 'name': f_name, 'type': 'private'})
        return jsonify({"success": True, "fonts": fonts})
    except Exception as e: return jsonify({"success": False, "error": str(e)})

@app.route('/api/get_assets')
def get_assets():
    font_id = request.args.get('font_id')
    user_id = request.args.get('user_id')
    assets = {}
    database = init_firebase()
    if database and font_id:
        doc = database.collection('fonts').document(font_id).get()
        if not doc.exists and user_id:
            doc = database.collection('users').document(user_id).collection('fonts').document(font_id).get()
        if doc.exists:
            harfler_data = doc.to_dict().get('harfler', {})
            for key, b64 in harfler_data.items():
                base_key = key.rsplit('_', 1)[0] if '_' in key else key
                if base_key not in assets: assets[base_key] = []
                assets[base_key].append(b64)
            return jsonify({"success": True, "assets": assets, "source": "firebase"})
    
    HARFLER_KLASORU = 'static/harfler'
    if os.path.exists(HARFLER_KLASORU):
        for dosya in os.listdir(HARFLER_KLASORU):
            if dosya.endswith('.png'):
                key = dosya.rsplit('_', 1)[0]
                if key not in assets: assets[key] = []
                assets[key].append(dosya)
        if assets: return jsonify({"success": True, "assets": assets, "source": "local"})
    return jsonify({"success": True, "assets": {}, "source": "none", "warning": "Harf yok"}), 200

# --- YENİ TARAMA ENDPOINTİ (2 BÖLÜM) ---
@app.route('/process_dual', methods=['POST'])
def process_dual():
    """Her sayfada 2 bölüm tara (üst + alt)"""
    global init_error
    try:
        data = request.get_json()
        u_id = data.get('user_id')
        f_name = data.get('font_name')
        image1_b64 = data.get('image1')  # Üst bölüm
        image2_b64 = data.get('image2')  # Alt bölüm
        
        if not u_id or not image1_b64 or not image2_b64:
            return jsonify({'success': False, 'message': 'Eksik veri (2 görsel gerekli)'}), 400
        
        # İki bölümü işle
        results = []
        all_harfler = {}
        
        for idx, b64_data in enumerate([image1_b64, image2_b64], 1):
            nparr = np.frombuffer(base64.b64decode(b64_data), np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            
            res, err = sistem.process_section(img, idx-1)
            if err:
                return jsonify({
                    'success': False,
                    'message': f'Bölüm {idx} hatası: {err}'
                }), 400
            
            results.append(res)
            all_harfler.update(res['harfler'])
        
        # Firebase'e kaydet
        database = init_firebase()
        if database:
            try:
                fid = f"{u_id}_{f_name.replace(' ', '_')}"
                d_ref = database.collection('fonts').document(fid)
                u_ref = database.collection('users').document(u_id).collection('fonts').document(fid)
                doc = d_ref.get()
                
                section_ids = [r['section_id'] for r in results]
                
                payload = {
                    'harfler': all_harfler,
                    'harf_sayisi': len(all_harfler),
                    'sections_completed': section_ids
                }
                
                if not doc.exists:
                    payload.update({
                        'owner_id': u_id,
                        'user_id': u_id,
                        'font_name': f_name,
                        'font_id': fid,
                        'created_at': firestore.SERVER_TIMESTAMP
                    })
                    d_ref.set(payload)
                    u_ref.set(payload)
                else:
                    curr = doc.to_dict()
                    h = curr.get('harfler', {})
                    h.update(all_harfler)
                    s = curr.get('sections_completed', [])
                    for sid in section_ids:
                        if sid not in s: s.append(sid)
                    
                    payload = {
                        'harfler': h,
                        'harf_sayisi': len(h),
                        'sections_completed': s,
                        'font_id': fid
                    }
                    d_ref.update(payload)
                    u_ref.update(payload)
                    
            except Exception as e:
                print(f"❌ DB Kayıt Hatası: {e}")
        
        return jsonify({
            'success': True,
            'section_ids': section_ids,
            'detected_chars': sum(r['detected'] for r in results),
            'total_chars': len(all_harfler),
            'db_project_id': connected_project_id
        })
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/download', methods=['POST'])
def download():
    try:
        metin = request.form.get('metin', '')
        font_id = request.form.get('font_id')
        user_id = request.form.get('user_id')
        yazi_boyutu = int(request.form.get('yazi_boyutu', 140))
        satir_araligi = int(request.form.get('satir_araligi', 220))
        kelime_boslugu = int(request.form.get('kelime_boslugu', 55))
        jitter = int(request.form.get('jitter', 3))
        murekkep_rengi_str = request.form.get('murekkep_rengi', 'tukenmez')
        paper_type = request.form.get('paper_type', 'cizgili')

        active_harfler = {}
        database = init_firebase()
        if database and font_id:
            doc = database.collection('fonts').document(font_id).get()
            if not doc.exists and user_id:
                doc = database.collection('users').document(user_id).collection('fonts').document(font_id).get()
            
            if doc.exists:
                raw_harfler = doc.to_dict().get('harfler', {})
                for key, b64_data in raw_harfler.items():
                    try:
                        if "," in b64_data: b64_data = b64_data.split(",")[1]
                        img_data = base64.b64decode(b64_data)
                        img = core_generator.Image.open(io.BytesIO(img_data)).convert("RGBA")
                        base_key = key.rsplit('_', 1)[0] if '_' in key else key
                        if base_key not in active_harfler: active_harfler[base_key] = []
                        active_harfler[base_key].append(img)
                    except: continue

        if not active_harfler:
            HARFLER_KLASORU = 'static/harfler'
            if os.path.exists(HARFLER_KLASORU):
                active_harfler = core_generator.harf_resimlerini_yukle(HARFLER_KLASORU)

        renkler = {
            'tukenmez': (27, 27, 29),
            'bic_mavi': (0, 35, 102),
            'pilot_mavi': (0, 51, 153),
            'eski_murekkep': (40, 60, 120),
            'kirmizi': (180, 20, 20),
            'lacivert': (24, 18, 110)
        }
        murekkep = renkler.get(murekkep_rengi_str, renkler['tukenmez'])

        config = {
            'page_width': 2480,
            'page_height': 3508,
            'margin_top': 200,
            'margin_left': 150,
            'margin_right': 150,
            'target_letter_height': yazi_boyutu,
            'line_spacing': satir_araligi,
            'word_spacing': kelime_boslugu,
            'murekkep_rengi': murekkep,
            'opacity': 0.95,
            'jitter': jitter,
            'paper_type': paper_type,
            'line_slope': 5
        }

        sayfalar = core_generator.metni_sayfaya_yaz(metin, active_harfler, config)
        pdf_buffer = core_generator.sayfalari_pdf_olustur(sayfalar)
        
        if pdf_buffer:
            return send_file(pdf_buffer, mimetype='application/pdf', as_attachment=True, download_name='el_yazisi.pdf')
        return "Hata", 500
    except Exception as e:
        traceback.print_exc()
        return str(e), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
