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

# --- FIREBASE BAĞLANTISI ---
db = None
connected_project_id = "BILINMIYOR"
init_error = None

def init_firebase():
    global db, init_error, connected_project_id
    if db is not None:
        return db

    try:
        cred = None
        # 1. Env Var (Render)
        env_creds = os.environ.get('FIREBASE_CREDENTIALS')
        if env_creds:
            try:
                cred_dict = json.loads(env_creds.strip())
                cred = credentials.Certificate(cred_dict)
                connected_project_id = cred_dict.get('project_id', 'EnvJson')
            except Exception as e:
                init_error = f"Env JSON Hatası: {e}"

        # 2. Env Vars (Parçalı)
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
            except Exception as e:
                init_error = f"Env Vars Hatası: {e}"

        # 3. Dosya (Local)
        if not cred:
            paths = ['serviceAccountKey.json', '/etc/secrets/serviceAccountKey.json']
            for p in paths:
                if os.path.exists(p):
                    try:
                        cred = credentials.Certificate(p)
                        with open(p, 'r') as f: connected_project_id = json.load(f).get('project_id', 'Dosya')
                        break
                    except Exception as e: init_error = f"Dosya Hatası ({p}): {e}"

        if cred:
            if not firebase_admin._apps: firebase_admin.initialize_app(cred)
            db = firestore.client()
            print(f"Firestore BAĞLANDI. Proje: {connected_project_id}")
            init_error = None
        else:
            if not firebase_admin._apps: firebase_admin.initialize_app()
            db = firestore.client()
            connected_project_id = "Default"
            init_error = None

    except Exception as e:
        print(f"FIREBASE KRITIK HATA: {e}")
        init_error = str(e)
        db = None
    
    return db

init_firebase()

class HarfSistemi:
    def __init__(self):
        self.char_list = []
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
            aruco_dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
            parameters = cv2.aruco.DetectorParameters()
            parameters.adaptiveThreshWinSizeMin = 3; parameters.adaptiveThreshWinSizeMax = 23; parameters.adaptiveThreshWinSizeStep = 5; parameters.minMarkerPerimeterRate = 0.01
            detector = cv2.aruco.ArucoDetector(aruco_dict, parameters)
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8,8))
            variations = [gray, clahe.apply(gray), cv2.threshold(gray, 128, 255, cv2.THRESH_BINARY)[1], cv2.threshold(clahe.apply(gray), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]]
            best_corners, best_ids = None, None
            for v in variations:
                corners, ids, _ = detector.detectMarkers(v)
                if ids is not None:
                    if best_ids is None or len(ids) > len(best_ids): best_corners, best_ids = corners, ids
                    if len(ids) >= 4: break
            return best_corners, best_ids
        except: return None, None

    def process_single_page(self, img, section_id):
        corners, ids = self.detect_markers(img)
        if ids is None or len(ids) < 4: return None, f"Markerlar bulunamadı ({len(ids) if ids else 0}/4)."
        ids = ids.flatten()
        base = int(min(ids))
        bid = int(base // 4)
        scale = 10; sw, sh = 210 * scale, 148 * scale; m = 175
        targets = [bid*4, bid*4+1, bid*4+2, bid*4+3]
        src_points = []
        for target in targets:
            found = False
            for i in range(len(ids)):
                if int(ids[i]) == target: src_points.append(np.mean(corners[i][0], axis=0)); found = True; break
            if not found: return None, f"Marker {target} eksik."
        src = np.float32(src_points)
        dst = np.float32([[m,m], [sw-m,m], [m,sh-m], [sw-m,sh-m]])
        warped = cv2.warpPerspective(img, cv2.getPerspectiveTransform(src, dst), (sw, sh))
        b_px = 150; sx, sy = int((sw - 1500)/2), int((sh - 900)/2); start_idx = bid * 60
        page_results = {}; detected_count = 0; missing_chars = []
        for r in range(6):
            for c in range(10):
                idx = start_idx + (r * 10 + c)
                if idx >= len(self.char_list): break
                p = 15; roi = warped[sy+r*b_px+p : sy+r*b_px+b_px-p, sx+c*b_px+p : sx+c*b_px+b_px-p]
                res = self.process_roi(roi)
                
                # FIX: Gürültü filtresini kaldırdım (> 0). Her şeyi kaydet.
                # FIX: Base64 string'i temizle (.replace)
                if res is not None and np.count_nonzero(res[:,:,3]) > 0:
                    _, buffer = cv2.imencode(".png", res)
                    b64_str = base64.b64encode(buffer).decode('utf-8').replace('\n', '')
                    page_results[self.char_list[idx]] = b64_str
                    detected_count += 1
                else: missing_chars.append(self.char_list[idx])
        return {'harfler': page_results, 'detected': detected_count, 'total': min(60, len(self.char_list)-start_idx), 'missing': missing_chars, 'section_id': bid}, None

sistem = HarfSistemi()

@app.route('/')
def home():
    status = f"BAGLI (Proje: {connected_project_id})" if db else f"BAGLANTI YOK: {init_error}"
    return jsonify({'status': 'ok', 'engine': 'aruco_v12_force_visible', 'db_status': status})

@app.route('/process_single', methods=['POST'])
def process_single():
    global init_error
    try:
        data = request.get_json()
        u_id, f_name, b64 = data.get('user_id'), data.get('font_name'), data.get('image_base64')
        if not u_id or not b64: return jsonify({'success': False, 'message': 'Eksik veri'}), 400
        nparr = np.frombuffer(base64.b64decode(b64), np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        res, err = sistem.process_single_page(img, 0)
        if err: return jsonify({'success': False, 'message': err}), 400

        db_debug_info = "Kayit Basarili"
        saved_path = "Yok"
        database = init_firebase()
        
        if database:
            try:
                fid = f"{u_id}_{f_name.replace(' ', '_')}"
                d_ref = database.collection('fonts').document(fid)
                u_ref = database.collection('users').document(u_id).collection('fonts').document(fid)
                saved_path = f"fonts/{fid} ve users/{u_id}/fonts/{fid}"
                doc = d_ref.get()
                payload = {'harfler': res['harfler'], 'harf_sayisi': len(res['harfler']), 'sections_completed': [res['section_id']]}
                if not doc.exists:
                    payload.update({'owner_id': u_id, 'user_id': u_id, 'font_name': f_name, 'created_at': firestore.SERVER_TIMESTAMP})
                    d_ref.set(payload); u_ref.set(payload)
                else:
                    curr = doc.to_dict()
                    h = curr.get('harfler', {}); h.update(res['harfler'])
                    s = curr.get('sections_completed', []); 
                    if res['section_id'] not in s: s.append(res['section_id'])
                    payload = {'harfler': h, 'harf_sayisi': len(h), 'sections_completed': s}
                    d_ref.update(payload); u_ref.update(payload)
            except Exception as e:
                db_debug_info = f"YAZMA HATASI: {str(e)}"
        else:
            db_debug_info = f"BAGLANTI YOK: {init_error}"

        return jsonify({
            'success': True,
            'section_id': res['section_id'],
            'detected_chars': res['detected'],
            'db_project_id': connected_project_id,
            'db_save_path': saved_path,
            'db_status': db_debug_info
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'message': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
