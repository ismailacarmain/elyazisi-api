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
from pdf2image import convert_from_bytes
import threading
import uuid
import requests
import shutil

app = Flask(__name__, template_folder='templates', static_folder='static')
CORS(app)

# --- CONFIG ---
RECAPTCHA_SECRET_KEY = os.environ.get('RECAPTCHA_SECRET_KEY', '6LcLXEYsAAAAAJfHokIfnc4v3MrhSKHX4gqbY8AX') # Fallback for dev

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
                private_key = os.environ.get('FIREBASE_PRIVATE_KEY', "").replace('\n', '\n')
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
            print(f"Firestore BAĞLANDI: {connected_project_id}")
        else:
            print("UYARI: Firebase credentials bulunamadı.")
    except Exception as e:
        init_error = str(e)
        db = None
        print(f"Firebase Hatası: {e}")
    return db

init_firebase()

# --- HARF TARAMA MOTORU (GELİŞMİŞ ARUCO SİSTEMİ) ---
class HarfSistemi:
    def __init__(self, repetition=3):
        self.repetition = repetition
        self.char_list = []
        self.generate_char_list()

    def generate_char_list(self):
        # Web uyumlu ASCII isimlendirme
        lowers = "abcçdefgğhıijklmnoöpqrsştuüvwxyz"
        uppers = "ABCÇDEFGĞHIİJKLMNOÖPQRSŞTUÜVWXYZ"
        digits = "0123456789"
        symbols_str = ".,:;?!-_\"'()[]{}/\\|+*=< >%^~@$€₺#"
        symbols_str = symbols_str.replace(" ", "")
        
        # Türkçe ve Özel Karakter Haritası (engine.js ile %100 uyumlu olmalı)
        # Önemli: I -> buyuk_ii, İ -> buyuk_i, ı -> kucuk_ii, i -> kucuk_i
        tr_map = {
            'ç': 'cc', 'ğ': 'gg', 'ı': 'ii', 'ö': 'oo', 'ş': 'ss', 'ü': 'uu',
            'Ç': 'cc', 'Ğ': 'gg', 'I': 'ii', 'İ': 'i', 'Ö': 'oo', 'Ş': 'ss', 'Ü': 'uu'
        }
        
        # Sembol haritası
        sym_map = {
            ".": "nokta", ",": "virgul", ":": "ikiknokta", ";": "noktalivirgul", 
            "?": "soru", "!": "unlem", "-": "tire", "_": "alt_tire",
            "\"": "tirnak", "'": "tektirnak", 
            "(": "parantezac", ")": "parantezkapama",
            "[": "koseli_ac", "]": "koseli_kapa",
            "{": "suslu_ac", "}": "suslu_kapa",
            "/": "slash", "\\": "backslas", "|": "pipe",
            "+": "arti", "*": "carpi", "=": "esit",
            "<": "kucuktur", ">": "buyuktur",
            "%": "yuzde", "^": "sapka", "~": "yaklasik",
            "@": "at", "$": "dolar", "€": "euro", "₺": "tl",
            "&": "ampersand", "#": "diyez"
        }

        # Küçük harfler
        for char in lowers:
            base = tr_map.get(char, char)
            for i in range(1, self.repetition + 1):
                self.char_list.append(f"kucuk_{base}_{i}")
        
        # Büyük harfler
        for char in uppers:
            # tr_map içinde varsa onu kullan (I->ii gibi), yoksa lowercase yap
            if char in tr_map:
                base = tr_map[char]
            else:
                base = char.lower()
            
            for i in range(1, self.repetition + 1):
                self.char_list.append(f"buyuk_{base}_{i}")
        
        # Rakamlar
        for char in digits:
            for i in range(1, self.repetition + 1):
                self.char_list.append(f"rakam_{char}_{i}")
        
        # Semboller
        seen = set()
        unique_symbols = ""
        for char in symbols_str:
            if char not in seen:
                unique_symbols += char
                seen.add(char)

        for char in unique_symbols:
            safe = sym_map.get(char, f"sembol_{ord(char)}")
            for i in range(1, self.repetition + 1):
                self.char_list.append(f"ozel_{safe}_{i}")

    def crop_tight(self, binary_img):
        coords = cv2.findNonZero(binary_img)
        if coords is None: return None
        x, y, w, h = cv2.boundingRect(coords)
        # Çok küçük gürültüleri ele (Noktalama işaretleri için limiti düşürdüm: 2px)
        if w < 2 or h < 2: return None
        return binary_img[y:y+h, x:x+w]

    def process_roi(self, roi):
        if roi.size == 0: return None
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        
        # Hafif bir yumuşatma
        gray = cv2.GaussianBlur(gray, (3,3), 0)
        
        # Adaptive Threshold
        thresh = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 21, 10)
        
        # --- ÇERÇEVE TEMİZLİĞİ (Gelişmiş) ---
        # Eğer threshold sonucu ROI kenarlarına çok yakınsa (çerçeve çizgisi), onu maskele.
        h_roi, w_roi = thresh.shape
        # Kenarlardan 3px içeri kadar olan kısımları sıfırla (siyah yap)
        cv2.rectangle(thresh, (0, 0), (w_roi, 3), 0, -1) # Üst
        cv2.rectangle(thresh, (0, h_roi-3), (w_roi, h_roi), 0, -1) # Alt
        cv2.rectangle(thresh, (0, 0), (3, h_roi), 0, -1) # Sol
        cv2.rectangle(thresh, (w_roi-3, 0), (w_roi, h_roi), 0, -1) # Sağ
        
        tight = self.crop_tight(thresh)
        if tight is None: return None
        
        h, w = tight.shape
        rgba = np.zeros((h, w, 4), dtype=np.uint8)
        rgba[:, :, 0] = 0
        rgba[:, :, 1] = 0
        rgba[:, :, 2] = 0
        rgba[:, :, 3] = tight
        
        return rgba

    def process_single_page(self, img, forced_section_id=None):
        # ... (Marker tespit kodları aynı kalıyor) ...
        # Marker tespiti için grayscale yap
        gray_full = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        
        aruco_dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
        parameters = cv2.aruco.DetectorParameters()
        parameters.adaptiveThreshWinSizeMin = 3
        parameters.adaptiveThreshWinSizeMax = 23
        parameters.adaptiveThreshWinSizeStep = 5
        
        detector = cv2.aruco.ArucoDetector(aruco_dict, parameters)
        
        corners, ids, _ = detector.detectMarkers(gray_full)
        
        # Eğer bulunamazsa kontrast artırıp tekrar dene
        if ids is None or len(ids) < 4:
            clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8,8))
            enhanced = clahe.apply(gray_full)
            corners, ids, _ = detector.detectMarkers(enhanced)
        
        if ids is None or len(ids) < 4:
            return None, f"Yetersiz marker ({0 if ids is None else len(ids)}/4). Lütfen fotoğrafı dik ve net çekin."
        
        ids = ids.flatten()
        
        # 2. Bölüm Tespiti
        if forced_section_id is not None:
            bid = forced_section_id
            start_id = bid * 4
            expected = [(start_id + k) % 50 for k in range(4)]
        else:
            base = int(min(ids))
            bid = base // 4
            start_id = bid * 4
            expected = [start_id, start_id+1, start_id+2, start_id+3]
        
        # 3. Perspektif
        src_points = []
        found_centers = {}
        for idx in range(len(ids)):
            found_centers[ids[idx]] = np.mean(corners[idx][0], axis=0)
            
        missing = []
        for target in expected:
            if target in found_centers: src_points.append(found_centers[target])
            else: missing.append(target)
                
        if missing: return None, f"Bölüm {bid} için markerlar eksik: {missing}"
            
        src = np.float32(src_points)
        scale = 10; sw, sh = 210 * scale, 148 * scale; m = 175
        dst = np.float32([[m, m], [sw-m, m], [m, sh-m], [sw-m, sh-m]])
        M = cv2.getPerspectiveTransform(src, dst)
        warped = cv2.warpPerspective(img, M, (sw, sh))
        
        # 4. Izgara Kesimi
        b_px = 150
        sx = int((sw - 10*b_px)/2)
        sy = int((sh - 6*b_px)/2)
        start_idx = bid * 60
        page_results = {}
        detected_count = 0
        
        for r in range(6):
            for c in range(10):
                idx = start_idx + (r * 10 + c)
                if idx >= len(self.char_list): continue
                
                # Padding optimizasyonu: Harflerin kesilmemesi için güvenli değer
                p = 20 
                roi = warped[sy+r*b_px+p : sy+r*b_px+b_px-p, sx+c*b_px+p : sx+c*b_px+b_px-p]
                
                processed_img = self.process_roi(roi)
                if processed_img is not None:
                    _, buffer = cv2.imencode(".png", processed_img)
                    b64_str = base64.b64encode(buffer).decode('utf-8').replace('\n', '')
                    page_results[self.char_list[idx]] = b64_str
                    detected_count += 1
                    
        return {'harfler': page_results, 'detected': detected_count, 'section_id': bid, 'total_in_section': min(60, len(self.char_list)-start_idx)}, None

# Varsayılan sistem (3x) - İstek üzerine değişebilir
default_sistem = HarfSistemi(repetition=3)

# --- LOCAL DATA HANDLING ---
LOCAL_DATA_DIR = 'local_data'
if not os.path.exists(LOCAL_DATA_DIR):
    try:
        os.makedirs(LOCAL_DATA_DIR)
    except: pass

def save_local_font(font_id, data):
    try:
        path = os.path.join(LOCAL_DATA_DIR, f"{font_id}.json")
        # datetime objelerini string yap
        if 'created_at' in data: data['created_at'] = str(data['created_at'])
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
        print(f"Local Font Kaydedildi: {path}")
    except Exception as e: 
        print(f"Local Save Hatası: {e}")

def get_local_font(font_id):
    try:
        path = os.path.join(LOCAL_DATA_DIR, f"{font_id}.json")
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e: 
        print(f"Local Get Hatası: {e}")
    return None

def list_local_fonts(user_id=None):
    fonts = []
    if not os.path.exists(LOCAL_DATA_DIR): return fonts
    for f in os.listdir(LOCAL_DATA_DIR):
        if f.endswith('.json') and not f.startswith('job_'):
            try:
                data = get_local_font(f.replace('.json', ''))
                if data:
                    # Filtreleme (user_id varsa sadece onun veya public olanlar)
                    if user_id:
                        if data.get('owner_id') == user_id or data.get('is_public'):
                            fonts.append({
                                'id': data.get('font_id'),
                                'name': data.get('font_name', 'Bilinmeyen'),
                                'type': 'public' if data.get('is_public') else 'private'
                            })
                    else:
                        fonts.append({
                            'id': data.get('font_id'),
                            'name': data.get('font_name', 'Bilinmeyen'),
                            'type': 'public' if data.get('is_public') else 'private'
                        })
            except: continue
    return fonts

def delete_local_font(font_id):
    try:
        path = os.path.join(LOCAL_DATA_DIR, f"{font_id}.json")
        if os.path.exists(path):
            os.remove(path)
            return True
    except: pass
    return False

def save_font_data(user_id, font_name, res, repetition, total_expected):
    database = init_firebase()
    
    # Payload Hazırlığı
    fid = f"{user_id}_{font_name.replace(' ', '_')}"
    new_harfler = res['harfler']
    new_section = res['section_id']
    
    # FIREBASE YOKSA LOCAL KAYDET
    if not database:
        existing = get_local_font(fid)
        if not existing:
            payload = {
                'harfler': new_harfler, 
                'harf_sayisi': len(new_harfler), 
                'sections_completed': [new_section],
                'owner_id': user_id, 
                'user_id': user_id, 
                'font_name': font_name, 
                'font_id': fid, 
                'repetition': repetition,
                'total_expected': total_expected,
                'is_public': False,
                'created_at': "NOW"
            }
        else:
            payload = existing
            if 'harfler' not in payload: payload['harfler'] = {}
            payload['harfler'].update(new_harfler)
            payload['harf_sayisi'] = len(payload['harfler'])
            
            s = payload.get('sections_completed', [])
            if new_section not in s: s.append(new_section)
            s.sort()
            payload['sections_completed'] = s
            
        save_local_font(fid, payload)
        return

    try:
        # SADECE Kullanıcı Koleksiyonuna Kaydet (Varsayılan olarak GİZLİ)
        u_ref = database.collection('users').document(user_id).collection('fonts').document(fid)
        
        # Global 'fonts' koleksiyonuna kaydetmiyoruz! Sadece 'toggle_visibility' ile oraya taşınacak.
        
        doc = u_ref.get()
        new_harfler = res['harfler']
        new_section = res['section_id']
        
        if not doc.exists:
            payload = {
                'harfler': new_harfler, 
                'harf_sayisi': len(new_harfler), 
                'sections_completed': [new_section],
                'owner_id': user_id, 
                'user_id': user_id, 
                'font_name': font_name, 
                'font_id': fid, 
                'repetition': repetition,
                'total_expected': total_expected,
                'is_public': False, # Varsayılan Gizli
                'created_at': firestore.SERVER_TIMESTAMP
            }
            u_ref.set(payload)
        else:
            curr = doc.to_dict()
            h = curr.get('harfler', {})
            h.update(new_harfler)
            
            s = curr.get('sections_completed', [])
            if new_section not in s: s.append(new_section)
            s.sort()
            
            payload = {
                'harfler': h, 
                'harf_sayisi': len(h), 
                'sections_completed': s, 
                'font_id': fid
            }
            u_ref.update(payload)
            
            # Eğer font daha önce Public yapıldıysa, oradaki veriyi de güncellemek gerekir
            # Ama şimdilik basit tutalım, kullanıcı tekrar yayınla derse güncellenir.
            
    except Exception as e: print(f"DB Kayıt Hatası: {e}")

def pdf_process_worker(job_id, file_bytes, user_id, font_name, repetition):
    try:
        database = init_firebase()
        
        # Local Status Dosyası (Firebase yoksa progress takibi için)
        local_job_path = os.path.join(LOCAL_DATA_DIR, f"job_{job_id}.json") if not database else None
        
        def update_job(data):
            if database:
                database.collection('operations').document(job_id).update(data)
            else:
                # Local Update
                current = {}
                if os.path.exists(local_job_path):
                    try: 
                        with open(local_job_path, 'r') as f: current = json.load(f)
                    except: pass
                current.update(data)
                with open(local_job_path, 'w') as f: json.dump(current, f)
        
        initial_status = {
            'status': 'processing', 'progress': 0,
            'current_section': 0,
            'message': 'PDF sayfaları işleniyor...', 
            'processed_chars': 0, 'total_chars': 0,
            'font_id': f"{user_id}_{font_name.replace(' ', '_')}"
        }

        if database:
            initial_status['total_sections'] = 0
            initial_status['created_at'] = firestore.SERVER_TIMESTAMP
            database.collection('operations').document(job_id).set(initial_status)
        else:
            with open(local_job_path, 'w') as f: json.dump(initial_status, f)

        # PDF -> Images
        images = convert_from_bytes(file_bytes, dpi=300, fmt='jpeg')
        total_sections = len(images) * 2
        update_job({'total_sections': total_sections})
        
        processed_chars_count = 0
        sections_done = 0
        failed_sections = []
        
        current_sistem = HarfSistemi(repetition=repetition)
        total_expected_chars = len(current_sistem.char_list)

        for i, pil_img in enumerate(images):
            update_job({'message': f'Sayfa {i+1} analiz ediliyor...'})

            open_cv_image = np.array(pil_img) 
            open_cv_image = open_cv_image[:, :, ::-1].copy() 
            
            h, w, _ = open_cv_image.shape
            half_h = h // 2
            parts = [open_cv_image[0:half_h, :], open_cv_image[half_h:h, :]]
            
            for part_idx, part_img in enumerate(parts):
                res, err = current_sistem.process_single_page(part_img)
                
                if res and res['detected'] > 0:
                     save_font_data(user_id, font_name, res, repetition, total_expected_chars)
                     processed_chars_count += len(res['harfler'])
                else:
                    failed_sections.append(sections_done + 1)
                    print(f"Bölüm {sections_done + 1} başarısız: {err}")
                
                sections_done += 1
                progress = int((sections_done / total_sections) * 100)
                
                update_job({
                    'progress': progress,
                    'current_section': sections_done,
                    'processed_chars': processed_chars_count,
                    'total_chars': total_expected_chars
                })

        final_msg = 'Tamamlandı.'
        if failed_sections:
            final_msg += f" (Uyarı: Bölüm {', '.join(map(str, failed_sections))} okunamadı. Işık/Netlik kontrol edin.)"

        update_job({
            'status': 'completed',
            'font_id': f"{user_id}_{font_name.replace(' ', '_')}",
            'progress': 100,
            'message': final_msg
        })

    except Exception as e:
        print(f"Worker Hatası: {e}")
        err_data = {'status': 'error', 'error': str(e)}
        if database:
            database.collection('operations').document(job_id).update(err_data)
        else:
            if 'local_job_path' in locals() and local_job_path:
                 with open(local_job_path, 'w') as f: json.dump(err_data, f)

# --- WEB ROTALARI ---

@app.route('/')
def index():
    font_id = request.args.get('font_id', '')
    user_id = request.args.get('user_id', '')
    return render_template('index.html', font_id=font_id, user_id=user_id)

@app.route('/mobil_yukle.html')
def mobil_page():
    # Statik dosya olarak sunmak yerine template render edebiliriz ama 
    # dosya yapısına göre statik sunum daha kolay olabilir.
    # Şimdilik template klasöründe olduğunu varsayalım ya da direkt static'den okuyalım.
    return send_file('web/mobil_yukle.html')

@app.route('/api/list_fonts')
def list_fonts():
    user_id = request.args.get('user_id')
    fonts = []
    database = init_firebase()
    
    if not database:
        return jsonify({"success": True, "fonts": list_local_fonts(user_id)})

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
    token = request.args.get('recaptcha_token')
    # Doğrulama (Opsiyonel: Sadece token varsa kontrol et)
    if token and not verify_recaptcha(token):
        if init_firebase(): # Sadece prod'da hata ver
            return jsonify({"success": False, "error": "Güvenlik doğrulaması başarısız"}), 403

    font_id = request.args.get('font_id')
    user_id = request.args.get('user_id')
    assets = {}
    database = init_firebase()
    
    if not database:
        # LOCAL GET
        if font_id:
            data = get_local_font(font_id)
            if data:
                harfler_data = data.get('harfler', {})
                for key, b64 in harfler_data.items():
                    base_key = key.rsplit('_', 1)[0] if '_' in key else key
                    if base_key not in assets: assets[base_key] = []
                    assets[base_key].append(b64)
                return jsonify({"success": True, "assets": assets, "source": "local_json"})
        
        # Fallback to static/harfler
        HARFLER_KLASORU = 'static/harfler'
        if os.path.exists(HARFLER_KLASORU):
            for dosya in os.listdir(HARFLER_KLASORU):
                if dosya.endswith('.png'):
                    key = dosya.rsplit('_', 1)[0]
                    if key not in assets: assets[key] = []
                    assets[key].append(dosya)
            return jsonify({"success": True, "assets": assets, "source": "local"})
        return jsonify({"success": True, "assets": {}, "source": "none", "warning": "Harf yok"}), 200

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

# --- TARAMA VE UPLOAD ---

@app.route('/process_single', methods=['POST'])
def process_single():
    global init_error
    try:
        data = request.get_json()
        
        # 1. reCAPTCHA Kontrolü
        recaptcha_token = data.get('recaptcha_token')
        if not verify_recaptcha(recaptcha_token):
             if init_firebase():
                return jsonify({'success': False, 'message': 'Güvenlik doğrulaması başarısız (Bot şüphesi).'}), 403

        u_id = data.get('user_id')
        f_name = data.get('font_name')
        b64 = data.get('image_base64')
        repetition = int(data.get('variation_count', 3))
        
        current_sistem = default_sistem
        if repetition != 3:
            current_sistem = HarfSistemi(repetition=repetition)

        if not u_id or not b64: return jsonify({'success': False, 'message': 'Eksik veri'}), 400
        
        nparr = np.frombuffer(base64.b64decode(b64), np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        if img is None: return jsonify({'success': False, 'message': 'Resim okunamadı'}), 400

        res, err = current_sistem.process_single_page(img)
        
        if err: return jsonify({'success': False, 'message': err}), 400

        total_chars = len(current_sistem.char_list)
        save_font_data(u_id, f_name, res, repetition, total_chars)

        return jsonify({
            'success': True,
            'section_id': res['section_id'],
            'detected_chars': res['detected'],
            'total_chars_found': len(res['harfler']),
            'db_project_id': connected_project_id
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'message': str(e)}), 500

def verify_recaptcha(token):
    """reCAPTCHA v2/v3 token doğrula"""
    if not token:
        print("reCAPTCHA Hatası: Token gönderilmedi.")
        return False
    
    if not RECAPTCHA_SECRET_KEY:
        print("UYARI: RECAPTCHA_SECRET_KEY tanımlanmamış!")
        return True

    try:
        response = requests.post('https://www.google.com/recaptcha/api/siteverify', data={
            'secret': RECAPTCHA_SECRET_KEY,
            'response': token
        })
        
        result = response.json()
        print(f"reCAPTCHA Sonucu: {result}")
        
        if result.get('success'):
            return True
            
        return False
    except Exception as e:
        print(f"reCAPTCHA Hatası: {e}")
        return False

@app.route('/api/upload_form', methods=['POST'])
def upload_form():
    try:
        # 1. reCAPTCHA Kontrolü
        recaptcha_token = request.form.get('recaptcha_token')
        if not verify_recaptcha(recaptcha_token):
             if init_firebase():
                return jsonify({'success': False, 'message': 'Güvenlik doğrulaması başarısız (Bot şüphesi).'}), 403

        user_id = request.form.get('user_id')
        font_name = request.form.get('font_name')
        variation_count = int(request.form.get('variation_count', 3))
        
        if 'file' not in request.files:
            return jsonify({'success': False, 'message': 'Dosya bulunamadı'}), 400
            
        file = request.files['file']
        if file.filename == '':
            return jsonify({'success': False, 'message': 'Dosya seçilmedi'}), 400

        job_id = str(uuid.uuid4())
        file_bytes = file.read()
        
        # Thread başlat
        thread = threading.Thread(target=pdf_process_worker, args=(job_id, file_bytes, user_id, font_name, variation_count))
        thread.start()
        
        return jsonify({'success': True, 'job_id': job_id, 'message': 'İşlem başlatıldı'})

    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/delete_font', methods=['POST'])
def delete_font():
    try:
        data = request.get_json()
        font_id = data.get('font_id')
        user_id = data.get('user_id')
        
        database = init_firebase()
        if not database:
             if delete_local_font(font_id):
                 return jsonify({'success': True, 'message': 'Font yerel diskten silindi'})
             return jsonify({'success': False, 'message': 'Font bulunamadı'}), 404

        # 1. Kullanıcının kendi koleksiyonundan sil
        user_font_ref = database.collection('users').document(user_id).collection('fonts').document(font_id)
        
        # Sahiplik kontrolü
        doc = user_font_ref.get()
        if not doc.exists:
            return jsonify({'success': False, 'message': 'Font bulunamadı veya yetkiniz yok'}), 404
            
        user_font_ref.delete()
        
        # 2. Genel 'fonts' koleksiyonundan da sil (Eğer varsa)
        public_font_ref = database.collection('fonts').document(font_id)
        if public_font_ref.get().exists:
            public_font_ref.delete()
            
        return jsonify({'success': True, 'message': 'Font başarıyla silindi'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/toggle_visibility', methods=['POST'])
def toggle_visibility():
    try:
        data = request.get_json()
        font_id = data.get('font_id')
        user_id = data.get('user_id')
        make_public = data.get('public', False) # True = Keşfete Ekle, False = Gizle
        
        database = init_firebase()
        if not database: return jsonify({'success': False, 'message': 'Veritabanı hatası (Local modda desteklenmiyor)'}), 500
        
        user_font_ref = database.collection('users').document(user_id).collection('fonts').document(font_id)
        doc = user_font_ref.get()
        
        if not doc.exists:
            return jsonify({'success': False, 'message': 'Font bulunamadı'}), 404
            
        font_data = doc.to_dict()
        public_font_ref = database.collection('fonts').document(font_id)
        
        if make_public:
            # Keşfete Ekle (Kopyala)
            font_data['type'] = 'public'
            public_font_ref.set(font_data)
            user_font_ref.update({'is_public': True})
        else:
            # Keşfetten Kaldır (Sil)
            if public_font_ref.get().exists:
                public_font_ref.delete()
            user_font_ref.update({'is_public': False})
            
        return jsonify({'success': True, 'message': 'Görünürlük güncellendi'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/copy_font', methods=['POST'])
def copy_font():
    try:
        data = request.get_json()
        source_font_id = data.get('font_id')
        user_id = data.get('user_id')
        
        database = init_firebase()
        if not database: return jsonify({'success': False, 'message': 'Local modda kopyalama desteklenmiyor'}), 500
        
        # 1. Kaynak fontu bul (Public koleksiyondan)
        src_ref = database.collection('fonts').document(source_font_id)
        doc = src_ref.get()
        
        if not doc.exists:
            return jsonify({'success': False, 'message': 'Font bulunamadı'}), 404
            
        font_data = doc.to_dict()
        
        # 2. Yeni ID oluştur (Çakışmayı önlemek için UUID ekle)
        import uuid
        new_font_id = f"{user_id}_{font_data.get('font_name', 'Kopya').replace(' ', '_')}_{str(uuid.uuid4())[:4]}"
        
        # 3. Meta verileri güncelle (Sahibi artık sensin)
        font_data['owner_id'] = user_id
        font_data['user_id'] = user_id
        font_data['font_id'] = new_font_id
        font_data['is_public'] = False # Kopyası gizli olur
        font_data['font_name'] = f"{font_data.get('font_name')} (Kopya)"
        font_data['original_source'] = source_font_id
        font_data['created_at'] = firestore.SERVER_TIMESTAMP
        
        # 4. Kullanıcının koleksiyonuna kaydet
        database.collection('users').document(user_id).collection('fonts').document(new_font_id).set(font_data)
        
        return jsonify({'success': True, 'message': 'Font kütüphanenize eklendi', 'new_font_id': new_font_id})
        
    except Exception as e:
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
        
        raw_harfler = {}
        if not database:
            if font_id:
                data = get_local_font(font_id)
                if data: raw_harfler = data.get('harfler', {})
        else:
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
                
                # Key formatını kontrol et (kucuk_a_1 -> kucuk_a)
                parts = key.rsplit('_', 1)
                if len(parts) > 1 and parts[1].isdigit():
                    base_key = parts[0]
                else:
                    base_key = key
                    
                if base_key not in active_harfler: active_harfler[base_key] = []
                active_harfler[base_key].append(img)
            except: continue

        if not active_harfler:
            HARFLER_KLASORU = 'static/harfler'
            if os.path.exists(HARFLER_KLASORU):
                active_harfler = core_generator.harf_resimlerini_yukle(HARFLER_KLASORU)

        renkler = {'tukenmez':(27,27,29), 'bic_mavi':(0,35,102), 'pilot_mavi':(0,51,153), 'eski_murekkep':(40,60,120), 'kirmizi':(180,20,20), 'lacivert':(24,18,110)}
        murekkep = renkler.get(murekkep_rengi_str, renkler['tukenmez'])

        config = {
            'page_width': 2480, 'page_height': 3508, 'margin_top': 200, 'margin_left': 150, 'margin_right': 150,
            'target_letter_height': yazi_boyutu, 'line_spacing': satir_araligi, 'word_spacing': kelime_boslugu,
            'murekkep_rengi': murekkep, 'opacity': 0.95, 'jitter': jitter, 'paper_type': paper_type, 'line_slope': 5
        }

        sayfalar = core_generator.metni_sayfaya_yaz(metin, active_harfler, config)
        pdf_buffer = core_generator.sayfalari_pdf_olustur(sayfalar)
        
        if pdf_buffer:
            return send_file(pdf_buffer, mimetype='application/pdf', as_attachment=True, download_name='el_yazisi.pdf')
        return "Hata", 500
    except Exception as e:
        traceback.print_exc()
        return str(e), 500

@app.route('/health')
def health_check():
    return "OK", 200

def keep_alive():
    """Render free tier sleep önleyici (Self-Ping)"""
    import time
    def ping():
        while True:
            time.sleep(840) # 14 dakika
            url = os.environ.get('RENDER_EXTERNAL_URL')
            if url:
                try:
                    # Self-ping at
                    if not url.endswith('/health'): 
                         if url.endswith('/'): url += 'health'
                         else: url += '/health'
                    requests.get(url)
                    print(f"Keep-Alive Ping gönderildi: {url}")
                except Exception as e:
                    print(f"Keep-Alive Hatası: {e}")
            else:
                print("Keep-Alive: RENDER_EXTERNAL_URL bulunamadı, ping atılamadı.")
                # URL yoksa döngüyü kırabiliriz veya tekrar deneyebiliriz, 
                # ama env var değişmeyeceği için kırmak mantıklı.
                break
    
    # Sadece production ortamında (Render) çalışsın
    if os.environ.get('RENDER'):
        t = threading.Thread(target=ping)
        t.daemon = True
        t.start()

if __name__ == '__main__':
    keep_alive()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))