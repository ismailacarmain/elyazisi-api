from flask import Flask, request, jsonify, render_template, send_file
from flask_cors import CORS
import cv2
import numpy as np
import os
import base64
import firebase_admin
from firebase_admin import credentials, firestore, storage
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
CORS(app)

# --- FIREBASE BAĞLANTISI ---
db = None
bucket = None
connected_project_id = "BILINMIYOR"
init_error = None

def init_firebase():
    global db, bucket, init_error, connected_project_id
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
            if not firebase_admin._apps: 
                # Storage Bucket adını project-id'den türet (Varsayılan)
                bucket_name = f"{connected_project_id}.appspot.com"
                firebase_admin.initialize_app(cred, {
                    'storageBucket': bucket_name
                })
            
            db = firestore.client()
            bucket = storage.bucket()
            print(f"Firestore ve Storage BAĞLANDI: {connected_project_id} -> {bucket.name}")
        else:
            print("UYARI: Firebase credentials bulunamadı.")
    except Exception as e:
        init_error = str(e)
        db = None
        print(f"Firebase Hatası: {e}")
    return db

init_firebase()

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

        # Küçük harfler
        for char in lowers:
            base = tr_map.get(char, char)
            for i in range(1, self.repetition + 1):
                self.char_list.append(f"kucuk_{base}_{i}")
        
        # Büyük harfler
        for char in uppers:
            if char in tr_map: base = tr_map[char]
            else: base = char.lower()
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
        if w < 2 or h < 2: return None
        return binary_img[y:y+h, x:x+w]

    def process_roi(self, roi):
        if roi.size == 0: return None
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (5,5), 0)
        # Scale 10 için BlockSize 51
        thresh = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 51, 10)
        tight = self.crop_tight(thresh)
        if tight is None: return None
        h, w = tight.shape
        rgba = np.zeros((h, w, 4), dtype=np.uint8)
        rgba[:, :, 3] = tight
        return rgba

    def process_single_page(self, img, forced_section_id=None):
        # ArUco Marker Tespiti
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
        
        if ids is None or len(ids) < 4:
            return None, f"Yetersiz marker ({0 if ids is None else len(ids)}/4)."
        
        ids = ids.flatten()
        
        # Bölüm Tespiti
        if forced_section_id is not None:
            bid = forced_section_id
            start_id = bid * 4
            expected = [(start_id + k) % 50 for k in range(4)]
        else:
            base = int(min(ids))
            bid = base // 4
            start_id = bid * 4
            expected = [start_id, start_id+1, start_id+2, start_id+3]
        
        # Perspektif Düzeltme
        src_points = []
        found_centers = {}
        for idx in range(len(ids)):
            found_centers[ids[idx]] = np.mean(corners[idx][0], axis=0)
            
        missing = []
        for target in expected:
            if target in found_centers: src_points.append(found_centers[target])
            else: missing.append(target)
                
        if missing: return None, f"Markerlar eksik: {missing}"
            
        src = np.float32(src_points)
        # YÜKSEK KALİTE (Scale 10)
        scale = 10; sw, sh = 210 * scale, 148 * scale; m = 175
        dst = np.float32([[m, m], [sw-m, m], [m, sh-m], [sw-m, sh-m]])
        M = cv2.getPerspectiveTransform(src, dst)
        warped = cv2.warpPerspective(img, M, (sw, sh))
        
        # Izgara Kesimi
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
                
                p = 15 
                roi = warped[sy+r*b_px+p : sy+r*b_px+b_px-p, sx+c*b_px+p : sx+c*b_px+b_px-p]
                
                processed_img = self.process_roi(roi)
                if processed_img is not None:
                    # PNG bytes döndür (Base64 DEĞİL)
                    _, buffer = cv2.imencode(".png", processed_img)
                    page_results[self.char_list[idx]] = buffer.tobytes()
                    detected_count += 1
                    
        return {'harfler': page_results, 'detected': detected_count, 'section_id': bid, 'total_in_section': min(60, len(self.char_list)-start_idx)}, None

# --- YARDIMCI: STORAGE UPLOAD ---
def upload_image_to_storage(img_bytes, path, content_type='image/png'):
    global bucket
    try:
        if bucket is None: return None
        blob = bucket.blob(path)
        blob.upload_from_string(img_bytes, content_type=content_type)
        blob.make_public()
        return blob.public_url
    except Exception as e:
        print(f"Upload Hatası ({path}): {e}")
        return None

# --- BACKGROUND WORKER ---
def process_pdf_job(job_id, user_id, font_name, variation_count, file_bytes):
    database = init_firebase()
    if not database: return
    
    op_ref = database.collection('operations').document(job_id)
    font_id = f"{user_id}_{font_name.replace(' ', '_')}"
    
    try:
        # 1. PDF'i Sayfalara Çevir (300 DPI)
        op_ref.update({'status': 'processing', 'message': 'PDF sayfalara dönüştürülüyor...', 'progress': 5})
        images = convert_from_bytes(file_bytes, dpi=300)
        
        # 2. Sayfaları Böl (Üst/Alt)
        sections_to_process = []
        for i, pil_img in enumerate(images):
            open_cv_image = np.array(pil_img) 
            open_cv_image = open_cv_image[:, :, ::-1].copy() # RGB to BGR
            
            h, w, _ = open_cv_image.shape
            half_h = h // 2
            
            top_half = open_cv_image[0:half_h, :]
            bottom_half = open_cv_image[half_h:h, :]
            
            sections_to_process.append({'img': top_half, 'id': i*2})
            sections_to_process.append({'img': bottom_half, 'id': i*2+1})

        # 3. Sırayla İşle ve Yükle
        harf_sistemi = HarfSistemi(repetition=variation_count)
        total_sections = len(sections_to_process)
        total_processed_chars = 0
        all_results = {}
        completed_sections = []

        for idx, section in enumerate(sections_to_process):
            current_progress = 10 + int((idx / total_sections) * 80)
            op_ref.update({
                'progress': current_progress, 
                'message': f'Bölüm {idx+1}/{total_sections} işleniyor ve yükleniyor...', 
                'current_section': idx + 1,
                'total_sections': total_sections
            })
            
            res, err = harf_sistemi.process_single_page(section['img'], forced_section_id=section['id'])
            
            if not err:
                # Elde edilen byte verilerini Storage'a yükle
                for char_name, img_bytes in res['harfler'].items():
                    storage_path = f"users/{user_id}/fonts/{font_id}/{char_name}.png"
                    public_url = upload_image_to_storage(img_bytes, storage_path)
                    if public_url:
                        all_results[char_name] = public_url
                
                total_processed_chars += res['detected']
                completed_sections.append(res['section_id'])
            else:
                print(f"Bölüm {section['id']} Hatası: {err}")

        # 4. Kaydet (URL'leri)
        op_ref.update({'status': 'saving', 'message': 'Veritabanına kaydediliyor...', 'progress': 95})
        
        d_ref = database.collection('fonts').document(font_id)
        u_ref = database.collection('users').document(user_id).collection('fonts').document(font_id)
        
        doc = d_ref.get()
        if doc.exists:
            current_data = doc.to_dict()
            existing_chars = current_data.get('harfler', {})
            existing_chars.update(all_results)
            
            existing_sections = current_data.get('sections_completed', [])
            for s in completed_sections:
                if s not in existing_sections: existing_sections.append(s)
            
            payload = {'harfler': existing_chars, 'harf_sayisi': len(existing_chars), 'sections_completed': existing_sections}
            d_ref.update(payload)
            u_ref.update(payload)
        else:
            payload = {
                'harfler': all_results, 'harf_sayisi': len(all_results), 
                'sections_completed': completed_sections,
                'owner_id': user_id, 'user_id': user_id, 
                'font_name': font_name, 'font_id': font_id, 
                'repetition': variation_count,
                'created_at': firestore.SERVER_TIMESTAMP
            }
            d_ref.set(payload)
            u_ref.set(payload)

        # 5. Tamamlandı
        op_ref.update({
            'status': 'completed', 
            'progress': 100, 
            'message': 'İşlem tamamlandı!',
            'processed_chars': total_processed_chars,
            'total_chars': len(harf_sistemi.char_list),
            'font_id': font_id
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
        user_id = request.form.get('user_id')
        font_name = request.form.get('font_name')
        variation_count = int(request.form.get('variation_count', 3))
        file = request.files.get('file')

        if not file or not user_id or not font_name:
            return jsonify({'success': False, 'message': 'Eksik bilgi'}), 400

        job_id = str(uuid.uuid4())
        file_bytes = file.read()
        
        # Firestore Job Kaydı
        if db:
            db.collection('operations').document(job_id).set({
                'status': 'queued',
                'progress': 0,
                'user_id': user_id,
                'created_at': firestore.SERVER_TIMESTAMP,
                'type': 'pdf_upload'
            })

        thread = threading.Thread(target=process_pdf_job, args=(job_id, user_id, font_name, variation_count, file_bytes))
        thread.start()

        return jsonify({'success': True, 'job_id': job_id, 'message': 'İşlem başlatıldı'})

    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/process_single', methods=['POST'])
def process_single():
    global init_error
    try:
        data = request.get_json()
        u_id = data.get('user_id')
        f_name = data.get('font_name')
        b64 = data.get('image_base64')
        repetition = int(data.get('variation_count', 3))
        
        current_sistem = HarfSistemi(repetition=repetition)
        if not u_id or not b64: return jsonify({'success': False, 'message': 'Eksik veri'}), 400
        
        nparr = np.frombuffer(base64.b64decode(b64), np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None: return jsonify({'success': False, 'message': 'Resim okunamadı'}), 400

        res, err = current_sistem.process_single_page(img)
        if err: return jsonify({'success': False, 'message': err}), 400
        
        # Storage Upload (Sync)
        uploaded_results = {}
        fid = f"{u_id}_{f_name.replace(' ', '_')}"
        
        if db and bucket:
            for char_name, img_bytes in res['harfler'].items():
                storage_path = f"users/{u_id}/fonts/{fid}/{char_name}.png"
                public_url = upload_image_to_storage(img_bytes, storage_path)
                if public_url:
                    uploaded_results[char_name] = public_url
            
            d_ref = db.collection('fonts').document(fid)
            u_ref = db.collection('users').document(u_id).collection('fonts').document(fid)
            
            doc = d_ref.get()
            new_section = res['section_id']
            
            if not doc.exists:
                payload = {
                    'harfler': uploaded_results, 'harf_sayisi': len(uploaded_results), 
                    'sections_completed': [new_section],
                    'owner_id': u_id, 'user_id': u_id, 'font_name': f_name, 'font_id': fid, 
                    'repetition': repetition
                }
                d_ref.set(payload); u_ref.set(payload)
            else:
                curr = doc.to_dict()
                h = curr.get('harfler', {}); h.update(uploaded_results)
                s = curr.get('sections_completed', []); 
                if new_section not in s: s.append(new_section)
                d_ref.update({'harfler': h, 'harf_sayisi': len(h), 'sections_completed': s})
                u_ref.update({'harfler': h, 'harf_sayisi': len(h), 'sections_completed': s})

        return jsonify({'success': True, 'section_id': res['section_id'], 'detected_chars': res['detected']})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/list_fonts')
def list_fonts():
    user_id = request.args.get('user_id')
    fonts = []
    database = init_firebase()
    if not database: return jsonify({"success": False, "error": "Veritabanı bağlantısı yok"})
    try:
        fonts_dict = {}
        try:
            public_fonts = database.collection('fonts').stream()
            for doc in public_fonts:
                d = doc.to_dict()
                fid = d.get('font_id', doc.id)
                fonts_dict[fid] = {
                    'id': fid, 'name': d.get('font_name') or fid, 'type': 'public',
                    'repetition': d.get('repetition', 3), 'char_count': d.get('harf_sayisi', 0),
                    'owner_id': d.get('owner_id', '')
                }
        except: pass

        if user_id:
            try:
                owner_query = database.collection('fonts').where('owner_id', '==', user_id).stream()
                for doc in owner_query:
                    d = doc.to_dict()
                    fid = d.get('font_id', doc.id)
                    fonts_dict[fid] = {
                        'id': fid, 'name': d.get('font_name') or fid, 'type': 'private',
                        'repetition': d.get('repetition', 3), 'char_count': d.get('harf_sayisi', 0),
                        'owner_id': user_id
                    }
            except: pass
        return jsonify({"success": True, "fonts": list(fonts_dict.values())})
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
            for key, val in harfler_data.items():
                base_key = key.rsplit('_', 1)[0] if '_' in key else key
                if base_key not in assets: assets[base_key] = []
                # val artık URL veya Base64 olabilir. Frontend bunu ayırt ediyor.
                assets[base_key].append(val)
            return jsonify({"success": True, "assets": assets, "source": "firebase"})
    return jsonify({"success": True, "assets": {}, "source": "none"}), 200

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
                for key, val in raw_harfler.items():
                    try:
                        img = None
                        if val.startswith('http'):
                            # URL'den indir
                            resp = requests.get(val)
                            img = core_generator.Image.open(io.BytesIO(resp.content)).convert("RGBA")
                        else:
                            # Base64
                            b64_data = val.split(",")[1] if "," in val else val
                            img_data = base64.b64decode(b64_data)
                            img = core_generator.Image.open(io.BytesIO(img_data)).convert("RGBA")
                        
                        parts = key.rsplit('_', 1)
                        base_key = parts[0] if len(parts) > 1 and parts[1].isdigit() else key
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

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))