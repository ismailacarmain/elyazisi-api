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
import threading
import uuid
import time
from pdf2image import convert_from_bytes
from PIL import Image as PILImage
import requests

app = Flask(__name__, template_folder='templates', static_folder='static')
# CORS Sıkılaştırması (Gevşetildi - Debug için)
CORS(app) # resources={r"/*": {"origins": "*"}})

# --- FIREBASE BAĞLANTISI ---
db = None
connected_project_id = "BILINMIYOR"
init_error = None

# --- GÜVENLİK (reCAPTCHA) ---
RECAPTCHA_SECRET_KEY = os.environ.get('RECAPTCHA_SECRET_KEY', "6LfEIUUsAAAAANamEZ_p_9PxSgx4hckW-9n9wI9e")

def verify_recaptcha(token):
    if not token: 
        print("reCAPTCHA Token yok!")
        # return False # Production'da False olmalı
        return True # Debug için geçici izin
    try:
        url = "https://www.google.com/recaptcha/api/siteverify"
        data = {'secret': RECAPTCHA_SECRET_KEY, 'response': token}
        res = requests.post(url, data=data)
        result = res.json()
        return result.get("success", False) and result.get("score", 0) >= 0.5
    except Exception as e:
        print(f"reCAPTCHA Hatası: {e}")
        return False

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
            print(f"Firestore BAĞLANDI (Limitsiz Mod): {connected_project_id}")
        else:
            print("UYARI: Firebase credentials bulunamadı.")
    except Exception as e:
        init_error = str(e)
        db = None
        print(f"Firebase Hatası: {e}")
    return db

init_firebase()

# --- KREDİ SİSTEMİ ---
def check_and_deduct_credit(user_id):
    try:
        if not db: return False, "Veritabanı hatası"
        user_ref = db.collection('users').document(user_id)
        doc = user_ref.get() 
        
        current_credits = 10 # Varsayılan başlangıç kredisi
        
        if doc.exists:
            data = doc.to_dict()
            current_credits = data.get('credits', 10)
        else:
            user_ref.set({'credits': 10}, merge=True)
            
        if current_credits <= 0:
            return False, "Yetersiz kredi! Yeni font oluşturmak için hakkınız kalmadı."
            
        user_ref.update({'credits': firestore.Increment(-1)})
        return True, current_credits - 1
    except Exception as e:
        print(f"Kredi Hatası: {e}")
        return False, str(e)

@app.route('/api/get_user_credits')
def get_user_credits():
    user_id = request.args.get('user_id')
    if not user_id or not db: return jsonify({'credits': 0})
    try:
        doc = db.collection('users').document(user_id).get()
        if doc.exists:
            return jsonify({'credits': doc.to_dict().get('credits', 10)})
        return jsonify({'credits': 10})
    except: return jsonify({'credits': 0})

# --- HARF TARAMA MOTORU ---
class HarfSistemi:
    def __init__(self, repetition=3):
        self.repetition = repetition
        self.char_list = []
        self.generate_char_list()

    def generate_char_list(self):
        lowers = "abcçdefgğhıijklmnoöpqrsştuüvwxyz"
        uppers = "ABCÇDEFGĞHIİJKLMNOÖPQRSŞTUÜVWXYZ"
        digits = "0123456789"
        symbols_str = ".,:;?!-_\"'()[]{}/\\|+*=< >%^~@$€₺#"
        symbols_str = symbols_str.replace(" ", "")
        
        tr_map = {
            'ç': 'cc', 'ğ': 'gg', 'ı': 'ii', 'ö': 'oo', 'ş': 'ss', 'ü': 'uu',
            'Ç': 'cc', 'Ğ': 'gg', 'I': 'ii', 'İ': 'i', 'Ö': 'oo', 'Ş': 'ss', 'Ü': 'uu'
        }
        
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

        for char in lowers:
            base = tr_map.get(char, char)
            for i in range(1, self.repetition + 1):
                self.char_list.append(f"kucuk_{base}_{i}")
        
        for char in uppers:
            base = tr_map.get(char, char.lower())
            for i in range(1, self.repetition + 1):
                self.char_list.append(f"buyuk_{base}_{i}")
        
        for char in digits:
            for i in range(1, self.repetition + 1):
                self.char_list.append(f"rakam_{char}_{i}")
        
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
        if w < 2 or h < 2: return None
        return binary_img[y:y+h, x:x+w]

    def process_roi(self, roi):
        if roi.size == 0: return None
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (5,5), 0)
        thresh = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 51, 10)
        tight = self.crop_tight(thresh)
        if tight is None: return None
        h, w = tight.shape
        rgba = np.zeros((h, w, 4), dtype=np.uint8)
        rgba[:, :, 3] = tight
        return rgba

    def process_single_page(self, img, forced_section_id=None):
        gray_full = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        aruco_dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
        parameters = cv2.aruco.DetectorParameters()
        parameters.adaptiveThreshWinSizeMin = 3
        parameters.adaptiveThreshWinSizeMax = 23
        parameters.adaptiveThreshWinSizeStep = 5
        
        detector = cv2.aruco.ArucoDetector(aruco_dict, parameters)
        corners, ids, _ = detector.detectMarkers(gray_full)
        
        if ids is None or len(ids) < 4:
            clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8,8))
            enhanced = clahe.apply(gray_full)
            corners, ids, _ = detector.detectMarkers(enhanced)
        
        if ids is None or len(ids) < 4: return None, f"Yetersiz marker ({0 if ids is None else len(ids)}/4)."
        ids = ids.flatten()
        
        bid = forced_section_id if forced_section_id is not None else int(min(ids)) // 4
        expected = [(bid * 4 + k) % 50 for k in range(4)]
        
        src_points = []
        found_centers = {ids[idx]: np.mean(corners[idx][0], axis=0) for idx in range(len(ids))}
        missing = [target for target in expected if target not in found_centers]
        if missing: return None, f"Markerlar eksik: {missing}"
            
        src = np.float32([found_centers[target] for target in expected])
        scale = 10; sw, sh = 210 * scale, 148 * scale; m = 175
        dst = np.float32([[m, m], [sw-m, m], [m, sh-m], [sw-m, sh-m]])
        matrix = cv2.getPerspectiveTransform(src, dst)
        warped = cv2.warpPerspective(img, matrix, (sw, sh))
        
        b_px = 150; sx = int((sw - 10*b_px)/2); sy = int((sh - 6*b_px)/2)
        start_idx = bid * 60; page_results = {}; detected_count = 0
        
        for r in range(6):
            for c in range(10):
                idx = start_idx + (r * 10 + c)
                if idx >= len(self.char_list): continue
                p = 15 
                roi = warped[sy+r*b_px+p : sy+r*b_px+b_px-p, sx+c*b_px+p : sx+c*b_px+b_px-p]
                processed_img = self.process_roi(roi)
                if processed_img is not None:
                    _, buffer = cv2.imencode(".png", processed_img)
                    b64_str = base64.b64encode(buffer).decode('utf-8')
                    page_results[self.char_list[idx]] = b64_str
                    detected_count += 1
                    
        return {'harfler': page_results, 'detected': detected_count, 'section_id': bid}, None

# --- BACKGROUND WORKER ---
def process_pdf_job(job_id, user_id, font_name, variation_count, file_bytes):
    database = init_firebase()
    if not database: return
    op_ref = database.collection('operations').document(job_id)
    font_id = f"{user_id}_{font_name.replace(' ', '_')}"
    
    try:
        op_ref.update({'status': 'processing', 'message': 'PDF sayfalara dönüştürülüyor...', 'progress': 5})
        images = convert_from_bytes(file_bytes, dpi=300)
        
        sections_to_process = []
        for i, pil_img in enumerate(images):
            cv_img = np.array(pil_img)[:, :, ::-1].copy()
            h, w, _ = cv_img.shape
            half_h = h // 2
            sections_to_process.append({'img': cv_img[0:half_h, :], 'id': i*2})
            sections_to_process.append({'img': cv_img[half_h:h, :], 'id': i*2+1})

        harf_sistemi = HarfSistemi(repetition=variation_count)
        total_sections = len(sections_to_process)
        total_processed_chars = 0
        all_completed_sections = []

        d_ref = database.collection('fonts').document(font_id)
        u_ref = database.collection('users').document(user_id).collection('fonts').document(font_id)
        
        if not d_ref.get().exists:
            init_payload = {
                'font_name': font_name, 'font_id': font_id, 'owner_id': user_id, 'user_id': user_id,
                'repetition': variation_count, 'created_at': firestore.SERVER_TIMESTAMP,
                'harf_sayisi': 0, 'sections_completed': [],
                'is_public': True # Public
            }
            d_ref.set(init_payload); u_ref.set(init_payload)

        for idx, section in enumerate(sections_to_process):
            op_ref.update({
                'progress': 10 + int((idx / total_sections) * 80), 
                'message': f'Bölüm {idx+1}/{total_sections} işleniyor...', 
                'current_section': idx + 1, 'total_sections': total_sections
            })
            
            res, err = harf_sistemi.process_single_page(section['img'], forced_section_id=section['id'])
            if not err:
                batch = database.batch()
                for char_name, b64 in res['harfler'].items():
                    char_ref = d_ref.collection('chars').document(char_name)
                    batch.set(char_ref, {'data': b64})
                batch.commit()
                
                total_processed_chars += res['detected']
                all_completed_sections.append(res['section_id'])

        current_doc = d_ref.get().to_dict()
        old_sections = current_doc.get('sections_completed', [])
        for s in all_completed_sections:
            if s not in old_sections: old_sections.append(s)
        
        final_meta = {
            'harf_sayisi': current_doc.get('harf_sayisi', 0) + total_processed_chars,
            'sections_completed': old_sections
        }
        d_ref.update(final_meta); u_ref.update(final_meta)

        op_ref.update({
            'status': 'completed', 'progress': 100, 'message': 'İşlem tamamlandı!',
            'processed_chars': total_processed_chars, 'font_id': font_id
        })
    except Exception as e:
        traceback.print_exc()
        op_ref.update({'status': 'error', 'error': str(e), 'progress': 0})

# --- WEB ROTALARI ---

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/mobil_yukle.html')
def mobil_page():
    return send_file('web/mobil_yukle.html')

@app.route('/api/upload_form', methods=['POST'])
def upload_form():
    try:
        # Güvenlik Kontrolü
        token = request.form.get('recaptcha_token')
        if not verify_recaptcha(token):
            return jsonify({'success': False, 'message': 'Güvenlik doğrulaması başarısız.'}), 403

        user_id = request.form.get('user_id')
        
        # Kredi Kontrolü
        allowed, msg = check_and_deduct_credit(user_id)
        if not allowed: return jsonify({'success': False, 'message': msg}), 402

        font_name = request.form.get('font_name')
        variation_count = int(request.form.get('variation_count', 3))
        file = request.files.get('file')
        if not file or not user_id or not font_name: return jsonify({'success': False, 'message': 'Eksik veri'}), 400
        job_id = str(uuid.uuid4())
        if db:
            db.collection('operations').document(job_id).set({
                'status': 'queued', 'progress': 0, 'user_id': user_id, 
                'created_at': firestore.SERVER_TIMESTAMP, 'type': 'pdf_upload'
            })
        threading.Thread(target=process_pdf_job, args=(job_id, user_id, font_name, variation_count, file.read())).start()
        return jsonify({'success': True, 'job_id': job_id})
    except Exception as e: return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/process_single', methods=['POST'])
def process_single():
    global init_error
    try:
        data = request.get_json()
        
        if not verify_recaptcha(data.get('recaptcha_token')):
            return jsonify({'success': False, 'message': 'Güvenlik doğrulaması başarısız.'}), 403

        u_id = data.get('user_id')
        
        # Kredi Kontrolü
        allowed, msg = check_and_deduct_credit(u_id)
        if not allowed: return jsonify({'success': False, 'message': msg}), 402

        f_name = data.get('font_name')
        b64 = data.get('image_base64')
        repetition = int(data.get('variation_count', 3))
        
        h_sistemi = HarfSistemi(repetition=repetition)
        nparr = np.frombuffer(base64.b64decode(b64), np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None: return jsonify({'success': False, 'message': 'Hata'}), 400

        res, err = h_sistemi.process_single_page(img)
        if err: return jsonify({'success': False, 'message': err}), 400
        
        if db:
            fid = f"{u_id}_{f_name.replace(' ', '_')}"
            d_ref = db.collection('fonts').document(fid)
            u_ref = db.collection('users').document(u_id).collection('fonts').document(fid)
            
            if not d_ref.get().exists:
                payload = {
                    'font_name': f_name, 'font_id': fid, 'owner_id': u_id, 'user_id': u_id,
                    'repetition': repetition, 'created_at': firestore.SERVER_TIMESTAMP,
                    'harf_sayisi': 0, 'sections_completed': [],
                    'is_public': True
                }
                d_ref.set(payload); u_ref.set(payload)
            
            batch = db.batch()
            for char_name, b64_char in res['harfler'].items():
                batch.set(d_ref.collection('chars').document(char_name), {'data': b64_char})
            batch.commit()
            
            curr = d_ref.get().to_dict()
            sects = curr.get('sections_completed', [])
            if res['section_id'] not in sects: sects.append(res['section_id'])
            
            upd = {'harf_sayisi': curr.get('harf_sayisi', 0) + res['detected'], 'sections_completed': sects}
            d_ref.update(upd); u_ref.update(upd)

        return jsonify({'success': True, 'section_id': res['section_id'], 'detected_chars': res['detected']})
    except Exception as e: return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/toggle_visibility', methods=['POST'])
def toggle_visibility():
    try:
        data = request.get_json()
        font_id = data.get('font_id')
        user_id = data.get('user_id')
        
        if not font_id or not user_id:
            return jsonify({'success': False, 'message': 'Eksik bilgi'}), 400
            
        database = init_firebase()
        if not database: return jsonify({'success': False, 'message': 'Veritabanı hatası'}), 500
        
        font_ref = database.collection('fonts').document(font_id)
        doc = font_ref.get()
        
        if not doc.exists:
            return jsonify({'success': False, 'message': 'Font bulunamadı'}), 404
            
        if doc.to_dict().get('owner_id') != user_id:
            return jsonify({'success': False, 'message': 'Yetkisiz işlem'}), 403
            
        current_status = doc.to_dict().get('is_public', True)
        new_status = not current_status
        font_ref.update({'is_public': new_status})
        
        return jsonify({'success': True, 'new_status': new_status})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/update_char', methods=['POST'])
def update_char():
    try:
        data = request.get_json()
        font_id = data.get('font_id')
        user_id = data.get('user_id')
        char_key = data.get('char_key')
        image_base64 = data.get('image_base64')
        
        if not all([font_id, user_id, char_key, image_base64]):
            return jsonify({'success': False, 'message': 'Eksik veri'}), 400
            
        database = init_firebase()
        if not database: return jsonify({'success': False, 'message': 'Veritabanı hatası'}), 500
        
        font_ref = database.collection('fonts').document(font_id)
        font_doc = font_ref.get()
        
        if not font_doc.exists: return jsonify({'success': False, 'message': 'Font bulunamadı'}), 404
        if font_doc.to_dict().get('owner_id') != user_id: return jsonify({'success': False, 'message': 'Yetkisiz işlem!'}), 403
            
        char_ref = font_ref.collection('chars').document(char_key)
        char_ref.set({'data': image_base64})
        
        return jsonify({'success': True, 'message': 'Harf güncellendi'})
    except Exception as e: return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/list_fonts')
def list_fonts():
    user_id = request.args.get('user_id')
    database = init_firebase()
    if not database: return jsonify({"success": False})
    fonts = []
    try:
        # Public
        public_query = database.collection('fonts').where('is_public', '==', True).stream()
        for doc in public_query:
            d = doc.to_dict()
            fonts.append({
                'id': doc.id, 'name': d.get('font_name'), 'char_count': d.get('harf_sayisi'),
                'type': 'public', 'owner_id': d.get('owner_id')
            })
            
        # Private (Login olmuşsa)
        if user_id:
            private_query = database.collection('fonts').where('owner_id', '==', user_id).where('is_public', '==', False).stream()
            for doc in private_query:
                d = doc.to_dict()
                fonts.append({
                    'id': doc.id, 'name': d.get('font_name'), 'char_count': d.get('harf_sayisi'),
                    'type': 'private', 'owner_id': user_id
                })
        return jsonify({"success": True, "fonts": fonts})
    except Exception as e: return jsonify({"success": False, "error": str(e)})

@app.route('/api/get_assets')
def get_assets():
    font_id = request.args.get('font_id')
    assets = {}
    database = init_firebase()
    if database and font_id:
        doc_ref = database.collection('fonts').document(font_id)
        
        # 1. YÖNTEM: Alt Koleksiyondan Çek (Yeni Sistem - Limitsiz)
        char_docs = doc_ref.collection('chars').stream()
        found_in_sub = False
        for doc in char_docs:
            found_in_sub = True
            key, val = doc.id, doc.to_dict().get('data')
            base_key = key.rsplit('_', 1)[0] if '_' in key else key
            if base_key not in assets: assets[base_key] = []
            assets[base_key].append(val)
            
        # 2. YÖNTEM: Ana Dokümandan Çek (Eski Sistem - Geriye Dönük Uyumluluk)
        if not found_in_sub:
            main_doc = doc_ref.get()
            if main_doc.exists:
                harfler_data = main_doc.to_dict().get('harfler', {})
                for key, val in harfler_data.items():
                    base_key = key.rsplit('_', 1)[0] if '_' in key else key
                    if base_key not in assets: assets[base_key] = []
                    assets[base_key].append(val)

        return jsonify({"success": True, "assets": assets, "source": "firebase"})
    return jsonify({"success": True, "assets": {}}), 200

@app.route('/download', methods=['POST'])
def download():
    try:
        font_id, metin = request.form.get('font_id'), request.form.get('metin', '')
        active_harfler = {}
        database = init_firebase()
        if database and font_id:
            # Önce alt koleksiyonu dene
            char_docs = database.collection('fonts').document(font_id).collection('chars').stream()
            has_chars = False
            for doc in char_docs:
                has_chars = True
                key, b64 = doc.id, doc.to_dict().get('data')
                img = core_generator.Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGBA")
                base_key = key.rsplit('_', 1)[0] if '_' in key else key
                if base_key not in active_harfler: active_harfler[base_key] = []
                active_harfler[base_key].append(img)
            
            # Alt koleksiyon boşsa eski sistemi dene
            if not has_chars:
                doc = database.collection('fonts').document(font_id).get()
                if doc.exists:
                    raw = doc.to_dict().get('harfler', {})
                    for key, val in raw.items():
                        try:
                            # Val base64 veya URL olabilir (Eski Storage denemesi)
                            if val.startswith('http'):
                                resp = requests.get(val)
                                img = core_generator.Image.open(io.BytesIO(resp.content)).convert("RGBA")
                            else:
                                b64 = val.split(",")[1] if "," in val else val
                                img = core_generator.Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGBA")
                            
                            base_key = key.rsplit('_', 1)[0] if '_' in key else key
                            if base_key not in active_harfler: active_harfler[base_key] = []
                            active_harfler[base_key].append(img)
                        except: continue
        
        config = {'page_width': 2480, 'page_height': 3508, 'margin_top': 200, 'margin_left': 150, 'margin_right': 150, 'target_letter_height': 140, 'line_spacing': 220, 'word_spacing': 55, 'murekkep_rengi': (27,27,29), 'opacity': 0.95, 'jitter': 3, 'paper_type': 'cizgili', 'line_slope': 5}
        sayfalar = core_generator.metni_sayfaya_yaz(metin, active_harfler, config)
        pdf_buffer = core_generator.sayfalari_pdf_olustur(sayfalar)
        return send_file(pdf_buffer, mimetype='application/pdf', as_attachment=True, download_name='el_yazisi.pdf')
    except: return "Hata", 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))