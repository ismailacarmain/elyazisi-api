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
from PIL import Image

app = Flask(__name__, template_folder='templates', static_folder='static')
CORS(app, resources={r"/*": {"origins": "*", "expose_headers": ["Content-Disposition"]}})

# --- FIREBASE CONNECTION ---
db = None
def init_firebase():
    global db
    if db is not None: return db
    try:
        cred = None
        env_creds = os.environ.get('FIREBASE_CREDENTIALS')
        if env_creds:
            cred = credentials.Certificate(json.loads(env_creds.strip()))
        
        if not cred:
            paths = ['serviceAccountKey.json', '/etc/secrets/serviceAccountKey.json']
            for p in paths:
                if os.path.exists(p):
                    cred = credentials.Certificate(p)
                    break
        
        if cred:
            if not firebase_admin._apps:
                firebase_admin.initialize_app(cred)
            db = firestore.client()
    except Exception as e:
        print(f"Firebase Error: {e}")
    return db

init_firebase()

# --- SCANNING ENGINE ---
class HarfSistemi:
    def __init__(self):
        self.char_list = []
        self.refresh_char_list(3)

    def refresh_char_list(self, variation_count=3):
        self.char_list = []
        for _, key in core_generator.KARAKTER_HARITASI.items():
            for i in range(1, variation_count + 1):
                self.char_list.append(f"{key}_{i}")

    def crop_tight(self, binary_img):
        coords = cv2.findNonZero(binary_img)
        if coords is None: return None
        x, y, w, h = cv2.boundingRect(coords)
        return binary_img[y:y+h, x:x+w]

    def process_roi(self, roi):
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        thresh = cv2.adaptiveThreshold(cv2.GaussianBlur(gray, (3,3), 0), 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 25, 12)
        tight = self.crop_tight(cv2.morphologyEx(thresh, cv2.MORPH_OPEN, np.ones((2,2), np.uint8)))
        if tight is None: return None
        res = np.full((tight.shape[0], tight.shape[1], 3), 255, dtype=np.uint8)
        res[tight == 255] = [0, 0, 0]
        return res

    def detect_markers(self, img):
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        aruco_dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
        try:
            params = cv2.aruco.DetectorParameters()
            detector = cv2.aruco.ArucoDetector(aruco_dict, params)
            corners, ids, _ = detector.detectMarkers(gray)
        except:
            corners, ids, _ = cv2.aruco.detectMarkers(gray, aruco_dict)
        return corners, ids

    def process_single_page(self, img, s_id):
        corners, ids = self.detect_markers(img)
        if ids is None or len(ids) < 4: return None, "Markerlar bulunamadı."
        ids = ids.flatten()
        base = int(min(ids))
        bid = int(base // 4)
        m = 175; sw, sh = 2100, 1480
        
        target_ids = [bid*4, bid*4+1, bid*4+2, bid*4+3]
        src_points = []
        for tid in target_ids:
            found = False
            for i in range(len(ids)):
                if ids[i] == tid:
                    src_points.append(np.mean(corners[i][0], axis=0))
                    found = True
                    break
            if not found: return None, f"Eksik marker: {tid}"
            
        warped = cv2.warpPerspective(img, cv2.getPerspectiveTransform(np.float32(src_points), np.float32([[m,m], [sw-m,m], [m,sh-m], [sw-m,sh-m]])), (sw, sh))
        
        b_px, start_idx = 150, bid * 30
        page_results = {}
        for r in range(3):
            for c in range(10):
                idx = start_idx + (r * 10 + c)
                if idx >= len(self.char_list): break
                roi = warped[290+r*150+15 : 290+r*150+135, 300+c*150+15 : 300+c*150+135]
                res = self.process_roi(roi)
                if res is not None:
                    _, buf = cv2.imencode(".png", res)
                    page_results[self.char_list[idx]] = base64.b64encode(buf).decode('utf-8')
        return {'harfler': page_results, 'detected': len(page_results), 'section_id': bid}, None

sistem = HarfSistemi()

# --- ROUTES ---

@app.route('/health')
def health(): return "OK", 200

@app.route('/api/generate_form')
def generate_form():
    try:
        v = int(request.args.get('variation_count', 3))
        sistem.refresh_char_list(v)
        pdf = core_generator.FormOlusturucu().tum_formu_olustur(v, False)
        return send_file(pdf, mimetype='application/pdf', as_attachment=True, download_name='form.pdf')
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route('/api/generate_example')
def generate_example():
    try:
        v = int(request.args.get('variation_count', 3))
        assets = core_generator.harf_resimlerini_yukle('static/harfler')
        pdf = core_generator.FormOlusturucu().tum_formu_olustur(v, True, assets)
        return send_file(pdf, mimetype='application/pdf', as_attachment=True, download_name='ornek.pdf')
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route('/api/get_assets')
def get_assets():
    f_id, u_id = request.args.get('font_id'), request.args.get('user_id')
    database = init_firebase()
    if database and f_id:
        doc = database.collection('fonts').document(f_id).get()
        if not doc.exists and u_id: doc = database.collection('users').document(u_id).collection('fonts').document(f_id).get()
        if doc.exists:
            data = doc.to_dict().get('harfler', {})
            assets = {}
            for k, v in data.items():
                base = k.rsplit('_', 1)[0]
                if base not in assets: assets[base] = []
                assets[base].append(v)
            return jsonify({"success": True, "assets": assets, "source": "firebase"})
    
    local_assets = {}
    if os.path.exists('static/harfler'):
        for f in os.listdir('static/harfler'):
            if f.endswith('.png'):
                key = f.rsplit('_', 1)[0]
                if key not in local_assets: local_assets[key] = []
                local_assets[key].append(f)
        return jsonify({"success": True, "assets": local_assets, "source": "local"})
    return jsonify({"success": False}), 404

@app.route('/process_single', methods=['POST'])
def process_single():
    try:
        data = request.get_json()
        u_id, f_name, b64, s_id, v_count = data['user_id'], data['font_name'], data['image_base64'], data.get('section_id', 0), data.get('variation_count', 3)
        file_type = data.get('file_type', 'image/jpeg')
        sistem.refresh_char_list(v_count)
        
        raw_bytes = base64.b64decode(b64)
        if 'pdf' in file_type.lower():
            images = convert_from_bytes(raw_bytes)
            if not images: return jsonify({'success': False, 'message': 'PDF error'}), 400
            img = cv2.cvtColor(np.array(images[0].convert('RGB')), cv2.COLOR_RGB2BGR)
        else:
            img = cv2.imdecode(np.frombuffer(raw_bytes, np.uint8), cv2.IMREAD_COLOR)
            
        res, err = sistem.process_single_page(img, s_id)
        if err: return jsonify({'success': False, 'message': err}), 400
        
        db = init_firebase()
        if db:
            fid = f"{u_id}_{f_name.replace(' ', '_')}"
            ref = db.collection('fonts').document(fid)
            u_ref = db.collection('users').document(u_id).collection('fonts').document(fid)
            doc = ref.get()
            if not doc.exists:
                payload = {'harfler': res['harfler'], 'owner_id': u_id, 'font_name': f_name, 'font_id': fid, 'variation_count': v_count, 'created_at': firestore.SERVER_TIMESTAMP}
                ref.set(payload); u_ref.set(payload)
            else:
                h = doc.to_dict().get('harfler', {}); h.update(res['harfler'])
                ref.update({'harfler': h}); u_ref.update({'harfler': h})
            db.collection('temp_scans').document(fid).set({f'section_{res["section_id"]}': True, 'last_update': firestore.SERVER_TIMESTAMP}, merge=True)
        return jsonify({'success': True, 'detected_chars': len(res['harfler'])})
    except Exception as e: return jsonify({'error': str(e)}), 500

@app.route('/download', methods=['POST'])
def download():
    try:
        f_id, u_id = request.form.get('font_id'), request.form.get('user_id')
        metin, size = request.form.get('metin', ''), int(request.form.get('yazi_boyutu', 140))
        spacing, w_spacing = int(request.form.get('satir_araligi', 220)), int(request.form.get('kelime_boslugu', 55))
        jitter, bold = int(request.form.get('jitter', 3)), int(float(request.form.get('kalinlik', 0)))
        paper = request.form.get('paper_type', 'duz')
        active_harfler = {}
        db = init_firebase()
        if db and f_id:
            doc = db.collection('fonts').document(f_id).get()
            if doc.exists:
                for k, v in doc.to_dict().get('harfler', {}).items():
                    base = k.rsplit('_', 1)[0]
                    if base not in active_harfler: active_harfler[base] = []
                    active_harfler[base].append(Image.open(io.BytesIO(base64.b64decode(v))).convert("RGBA"))
        if not active_harfler: active_harfler = core_generator.harf_resimlerini_yukle('static/harfler')
        config = {'page_width': 2480, 'page_height': 3508, 'margin_top': 200, 'margin_left': 150, 'margin_right': 150, 'target_letter_height': size, 'line_spacing': spacing, 'word_spacing': w_spacing, 'jitter': jitter, 'paper_type': paper, 'murekkep_rengi': (27,27,29), 'kalinlik': bold}
        sayfalar = core_generator.metni_sayfaya_yaz(metin, active_harfler, config)
        return send_file(core_generator.sayfalari_pdf_olustur(sayfalar), mimetype='application/pdf', as_attachment=True, download_name='yazi.pdf')
    except Exception as e: return str(e), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)