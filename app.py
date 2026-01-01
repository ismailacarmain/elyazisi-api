from flask import Flask, request, jsonify
from flask_cors import CORS
import cv2
import numpy as np
from PIL import Image
import os
import base64
import io
import firebase_admin
from firebase_admin import credentials, firestore
import uuid
import json
import traceback

app = Flask(__name__)
CORS(app)

# Firebase initialization
try:
    if not firebase_admin._apps:
        cred = None
        if os.path.exists('serviceAccountKey.json'):
            cred = credentials.Certificate('serviceAccountKey.json')
        elif os.environ.get('FIREBASE_CREDENTIALS'):
            cred_dict = json.loads(os.environ.get('FIREBASE_CREDENTIALS'))
            cred = credentials.Certificate(cred_dict)
        
        if cred:
            firebase_admin.initialize_app(cred)
        else:
            firebase_admin.initialize_app()
    db = firestore.client()
except Exception as e:
    print(f"Firebase Init Error: {e}")
    db = None

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

    def detect_markers(self, img):
        try:
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            aruco_dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
            parameters = cv2.aruco.DetectorParameters()
            if hasattr(cv2.aruco, 'ArucoDetector'):
                detector = cv2.aruco.ArucoDetector(aruco_dict, parameters)
                corners, ids, rejected = detector.detectMarkers(gray)
            else:
                corners, ids, rejected = cv2.aruco.detectMarkers(gray, aruco_dict, parameters=parameters)
            return corners, ids
        except Exception as e:
            return None, None

    def process_single_page(self, img, section_id):
        corners, ids = self.detect_markers(img)
        if ids is None or len(ids) < 4:
            return None, "Markerlar bulunamadı! Formu tam ve net çekin."
            
        ids = ids.flatten()
        base = int(min(ids)) # NumPy int32 -> Python int
        bid = int(base // 4) # NumPy int32 -> Python int
        
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
            if not found: return None, f"Marker {target} eksik!"

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
                
                gray_roi = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
                thresh = cv2.adaptiveThreshold(gray_roi, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 25, 12)
                
                coords = cv2.findNonZero(thresh)
                if coords is not None:
                    x, y, w, h = cv2.boundingRect(coords)
                    tight = thresh[y:y+h, x:x+w]
                    _, buffer = cv2.imencode(".png", tight)
                    png_base64 = base64.b64encode(buffer).decode('utf-8')
                    page_results[char_name] = png_base64
                    detected_count += 1
                else:
                    missing_chars.append(char_name)
                    
        return {
            'harfler': page_results,
            'detected': int(detected_count),
            'total': int(min(60, len(self.char_list) - start_idx)),
            'missing': missing_chars,
            'section_id': int(bid)
        }, None

sistem = HarfSistemi()

@app.route('/')
def home():
    return jsonify({'status': 'ok', 'engine': 'aruco_v2_fixed'})

@app.route('/process_single', methods=['POST'])
def process_single():
    try:
        data = request.get_json()
        user_id = data.get('user_id')
        font_name = data.get('font_name', 'Yeni Font')
        img_b64 = data.get('image_base64')

        if not user_id or not img_b64:
            return jsonify({'success': False, 'message': 'Eksik veri'}), 400

        nparr = np.frombuffer(base64.b64decode(img_b64), np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        result, error = sistem.process_single_page(img, 0)
        if error:
            return jsonify({'success': False, 'message': error}), 400

        if db:
            font_doc_id = f"{user_id}_{font_name.replace(' ', '_')}"
            doc_ref = db.collection('fonts').document(font_doc_id)
            doc = doc_ref.get()
            
            if not doc.exists:
                doc_ref.set({
                    'owner_id': user_id,
                    'font_name': font_name,
                    'harfler': result['harfler'],
                    'created_at': firestore.SERVER_TIMESTAMP,
                    'sections_completed': [result['section_id']]
                })
            else:
                current_data = doc.to_dict()
                new_harfler = current_data.get('harfler', {})
                new_harfler.update(result['harfler'])
                completed = current_data.get('sections_completed', [])
                if result['section_id'] not in completed: completed.append(result['section_id'])
                doc_ref.update({'harfler': new_harfler, 'sections_completed': completed})

        return jsonify({
            'success': True,
            'section_id': result['section_id'],
            'detected_chars': result['detected'],
            'total_chars': result['total'],
            'missing_chars': result['missing']
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
