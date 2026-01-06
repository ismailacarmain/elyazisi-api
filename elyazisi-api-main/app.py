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
CORS(app, resources={r"/*": {"origins": "*"}})

# --- FIREBASE BAĞLANTISI ---
db = None
def init_firebase():
    global db
    if db is not None: return db
    try:
        cred = None
        paths = ['serviceAccountKey.json', '/etc/secrets/serviceAccountKey.json']
        for p in paths:
            if os.path.exists(p):
                cred = credentials.Certificate(p)
                break
        if cred:
            if not firebase_admin._apps: firebase_admin.initialize_app(cred)
            db = firestore.client()
    except Exception as e: print(f"Firebase Hatası: {e}")
    return db

init_firebase()

# --- HARF TARAMA MOTORU ---
class HarfSistemi:
    def __init__(self):
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
        aruco_dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
        parameters = cv2.aruco.DetectorParameters()
        detector = cv2.aruco.ArucoDetector(aruco_dict, parameters)
        corners, ids, _ = detector.detectMarkers(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
        return corners, ids

    def process_single_page(self, img, section_id):
        corners, ids = self.detect_markers(img)
        if ids is None or len(ids) < 4: return None, "Markerlar bulunamadı."
        ids = ids.flatten()
        base = int(min(ids))
        bid = int(base // 4)
        scale = 10; sw, sh = 210 * scale, 148 * scale; m = 175
        src = np.float32([np.mean(corners[i][0], axis=0) for i in range(len(ids)) if ids[i] in [bid*4, bid*4+1, bid*4+2, bid*4+3]])
        if len(src) < 4: return None, "Eksik marker."
        warped = cv2.warpPerspective(img, cv2.getPerspectiveTransform(src, np.float32([[m,m], [sw-m,m], [m,sh-m], [sw-m,sh-m]])), (sw, sh))
        
        b_px, start_idx = 150, bid * 60
        page_results = {}
        for r in range(6):
            for c in range(10):
                idx = start_idx + (r * 10 + c)
                if idx >= len(self.char_list): break
                roi = warped[int((sh-900)/2)+r*b_px+15 : int((sh-900)/2)+r*b_px+b_px-15, int((sw-1500)/2)+c*b_px+15 : int((sw-1500)/2)+c*b_px+b_px-15]
                res = self.process_roi(roi)
                if res is not None:
                    _, buf = cv2.imencode(".png", res)
                    page_results[self.char_list[idx]] = base64.b64encode(buf).decode('utf-8')
        return {'harfler': page_results, 'detected': len(page_results), 'section_id': bid}, None

sistem = HarfSistemi()

# --- ROTALAR ---

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
            harfler = doc.to_dict().get('harfler', {})
            assets = {}
            for k, v in harfler.items():
                base = k.rsplit('_', 1)[0]
                if base not in assets: assets[base] = []
                assets[base].append(v)
            return jsonify({"success": True, "assets": assets, "source": "firebase"})
    return jsonify({"success": False}), 404

@app.route('/process_single', methods=['POST'])
def process_single():
    data = request.get_json()
    u_id, f_name, b64, s_id, v_count = data.get('user_id'), data.get('font_name'), data.get('image_base64'), data.get('section_id', 0), data.get('variation_count', 3)
    sistem.refresh_char_list(v_count)
    img = cv2.imdecode(np.frombuffer(base64.b64decode(b64), np.uint8), cv2.IMREAD_COLOR)
    res, err = sistem.process_single_page(img, s_id)
    if err: return jsonify({'success': False, 'message': err}), 400
    
    database = init_firebase()
    if database:
        fid = f"{u_id}_{f_name.replace(' ', '_')}"
        ref = database.collection('fonts').document(fid)
        doc = ref.get()
        if not doc.exists: 
            ref.set({'harfler': res['harfler'], 'owner_id': u_id, 'font_name': f_name, 'variation_count': v_count})
        else:
            h = doc.to_dict().get('harfler', {})
            h.update(res['harfler'])
            ref.update({'harfler': h})
        database.collection('temp_scans').document(fid).set({f'section_{res["section_id"]}': True}, merge=True)
    return jsonify({'success': True, 'detected_chars': res['detected']})

@app.route('/download', methods=['POST'])
def download():
    try:
        f_id, u_id = request.form.get('font_id'), request.form.get('user_id')
        size = int(request.form.get('yazi_boyutu', 140))
        bold = int(float(request.form.get('kalinlik', 0)))
        
        active_harfler = {}
        database = init_firebase()
        if database and f_id:
            doc = database.collection('fonts').document(f_id).get()
            if doc.exists:
                for k, v in doc.to_dict().get('harfler', {}).items():
                    base = k.rsplit('_', 1)[0]
                    if base not in active_harfler: active_harfler[base] = []
                    active_harfler[base].append(core_generator.Image.open(io.BytesIO(base64.b64decode(v))).convert("RGBA"))
        
        if not active_harfler: active_harfler = core_generator.harf_resimlerini_yukle('static/harfler')
        
        config = {
            'page_width': 2480, 'page_height': 3508, 'margin_top': 200, 'margin_left': 150, 'margin_right': 150,
            'target_letter_height': size, 'line_spacing': int(request.form.get('satir_araligi', 220)),
            'word_spacing': int(request.form.get('kelime_boslugu', 55)), 'jitter': int(request.form.get('jitter', 3)),
            'paper_type': request.form.get('paper_type', 'duz'), 'murekkep_rengi': (27,27,29), 'kalinlik': bold
        }
        
        pdf = core_generator.sayfalari_pdf_olustur(core_generator.metni_sayfaya_yaz(request.form.get('metin', ''), active_harfler, config))
        return send_file(pdf, mimetype='application/pdf', as_attachment=True, download_name='el_yazisi.pdf')
    except Exception as e: return str(e), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
