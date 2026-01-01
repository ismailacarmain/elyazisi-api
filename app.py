from flask import Flask, request, jsonify
from flask_cors import CORS
import cv2
import numpy as np
import os
import base64
import firebase_admin
from firebase_admin import credentials, firestore
import json

app = Flask(__name__)
CORS(app)

# Firebase initialization
if not firebase_admin._apps:
    cred = None
    if os.path.exists('serviceAccountKey.json'):
        cred = credentials.Certificate('serviceAccountKey.json')
    elif os.environ.get('FIREBASE_CREDENTIALS'):
        try:
            cred_dict = json.loads(os.environ.get('FIREBASE_CREDENTIALS'))
            cred = credentials.Certificate(cred_dict)
        except: pass
    if not cred and os.environ.get("FIREBASE_PROJECT_ID"):
        try:
            cred_dict = {
                "type": "service_account",
                "project_id": os.environ.get("FIREBASE_PROJECT_ID"),
                "private_key_id": os.environ.get("FIREBASE_PRIVATE_KEY_ID"),
                "private_key": os.environ.get("FIREBASE_PRIVATE_KEY", "").replace("\n", "\n"),
                "client_email": os.environ.get("FIREBASE_CLIENT_EMAIL"),
                "client_id": os.environ.get("FIREBASE_CLIENT_ID"),
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
            cred = credentials.Certificate(cred_dict)
        except: pass
    if cred:
        firebase_admin.initialize_app(cred)
    else:
        firebase_admin.initialize_app()

db = firestore.client()

class HarfSistemi:
    def __init__(self):
        self.char_list = []
        low, upp, num = "abcçdefgğhıijklmnoöprsştuüvyz", "ABCÇDEFGĞHIİJKLMNOÖPRSŞTUÜVYZ", "0123456789"
        punc = {".":"nokta", ",":"virgul", ":":"ikiknokta", ";":"noktalivirgul", "?":"soru", "!":"unlem", "-":"tire", "(":"parantezac", ")":"parantezkapama"}
        for c in low: [self.char_list.append(f"kucuk_{c}_{i}") for i in range(1,4)]
        for c in upp: [self.char_list.append(f"buyuk_{c}_{i}") for i in range(1,4)]
        for c in num: [self.char_list.append(f"rakam_{c}_{i}") for i in range(1,4)]
        for c, n in punc.items(): [self.char_list.append(f"ozel_{n}_{i}") for i in range(1,4)]

    def detect_markers(self, img):
        try:
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            aruco_dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
            parameters = cv2.aruco.DetectorParameters()
            if hasattr(cv2.aruco, 'ArucoDetector'):
                det = cv2.aruco.ArucoDetector(aruco_dict, parameters)
                corners, ids, _ = det.detectMarkers(gray)
            else:
                corners, ids, _ = cv2.aruco.detectMarkers(gray, aruco_dict, parameters=parameters)
            return corners, ids
        except: return None, None

    def process_roi(self, roi):
        # ROI İşleme (Hassas okuma)
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (3,3), 0)
        # Eşiklemeyi biraz daha yumuşattık (21, 7)
        thresh = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 21, 7)
        
        # Gürültü temizle
        kernel = np.ones((2,2), np.uint8)
        thresh = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel)
        
        # Harfi bul ve sıkıştır (Tight crop)
        coords = cv2.findNonZero(thresh)
        if coords is None or cv2.countNonZero(thresh) < 25: # Boş kutu kontrolü
            return None
            
        x, y, w, h = cv2.boundingRect(coords)
        tight = thresh[y:y+h, x:x+w]
        
        # SİYAH YAZI - BEYAZ ARKA PLAN
        # tight su an: yazı=255 (beyaz), arka plan=0 (siyah)
        # bitwise_not ile: yazı=0 (siyah), arka plan=255 (beyaz)
        final_img = cv2.bitwise_not(tight)
        return final_img

    def process_single_page(self, img):
        corners, ids = self.detect_markers(img)
        if ids is None or len(ids) < 4: return None, "Markerlar bulunamadı!"
        ids = ids.flatten()
        base = int(min(ids))
        bid = int(base // 4)
        targets = [bid*4, bid*4+1, bid*4+2, bid*4+3]
        src_points = []
        for t in targets:
            found = False
            for i in range(len(ids)):
                if int(ids[i]) == t:
                    src_points.append(np.mean(corners[i][0], axis=0))
                    found = True; break
            if not found: return None, f"Marker {t} eksik!"
        
        sw, sh = 2100, 1480
        src = np.float32(src_points)
        dst = np.float32([[175,175], [sw-175,175], [175,sh-175], [sw-175,sh-175]])
        warped = cv2.warpPerspective(img, cv2.getPerspectiveTransform(src, dst), (sw, sh))
        
        res_map = {}
        sx, sy, b = (sw-1500)//2, (sh-900)//2, 150
        s_idx = bid * 60
        for r in range(6):
            for c in range(10):
                idx = s_idx + (r*10 + c)
                if idx >= len(self.char_list): break
                # Kutuyu al (Hafif geniş kadraj)
                roi = warped[sy+r*b+10 : sy+r*b+b-10, sx+c*b+10 : sx+c*b+b-10]
                
                processed = self.process_roi(roi)
                if processed is not None:
                    _, buf = cv2.imencode(".png", processed)
                    res_map[self.char_list[idx]] = base64.b64encode(buf).decode('utf-8')
        return {'harfler': res_map, 'bid': bid}, None

tarayici = HarfSistemi()

@app.route('/')
def home(): return jsonify({'status': 'ok', 'engine': 'aruco_v8_final', 'db': db is not None})

@app.route('/process_single', methods=['POST'])
def process_single():
    try:
        data = request.get_json()
        u_id, f_name, b64 = data.get('user_id'), data.get('font_name', 'Font'), data.get('image_base64')
        if not u_id or not b64: return jsonify({'error': 'eksik veri'}), 400
        
        nparr = np.frombuffer(base64.b64decode(b64), np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        res, err = tarayici.process_single_page(img)
        if err: return jsonify({'error': err}), 400

        f_id = f"font_{u_id}_{f_name.replace(' ', '_')}"
        doc_ref = db.collection('fonts').document(f_id)
        user_ref = db.collection('users').document(u_id).collection('fonts').document(f_id)
        
        doc = doc_ref.get()
        if not doc.exists:
            payload = {
                'font_id': f_id, 'font_name': f_name, 'user_id': u_id, 'owner_id': u_id,
                'harf_sayisi': len(res['harfler']), 'harfler': res['harfler'],
                'created_at': firestore.SERVER_TIMESTAMP, 'status': 'completed',
                'sections': [res['bid']]
            }
            doc_ref.set(payload); user_ref.set(payload)
        else:
            old = doc.to_dict()
            h = old.get('harfler', {}); h.update(res['harfler'])
            s = old.get('sections', []); 
            if res['bid'] not in s: s.append(res['bid'])
            upd = {'harfler': h, 'harf_sayisi': len(h), 'sections': s}
            doc_ref.update(upd); user_ref.update(upd)

        return jsonify({'success': True, 'count': len(res['harfler'])})
    except Exception as e: return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
