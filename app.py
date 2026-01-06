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
from PIL import Image
import core_generator

app = Flask(__name__, template_folder='templates', static_folder='static')
CORS(app, resources={r"/*": {"origins": "*", "expose_headers": ["Content-Disposition"]}})

# --- FIREBASE BAĞLANTISI ---
db = None

def init_firebase():
    """Firebase bağlantısını başlatır"""
    global db
    if db is not None:
        return db
    
    try:
        cred = None
        
        # Environment variable'dan credentials al
        env_creds = os.environ.get('FIREBASE_CREDENTIALS')
        if env_creds:
            cred = credentials.Certificate(json.loads(env_creds.strip()))
        
        # Dosyadan credentials al
        if not cred:
            paths = ['serviceAccountKey.json', '/etc/secrets/serviceAccountKey.json']
            for p in paths:
                if os.path.exists(p):
                    cred = credentials.Certificate(p)
                    break
        
        # Firebase başlat
        if cred:
            if not firebase_admin._apps:
                firebase_admin.initialize_app(cred)
            db = firestore.client()
            print("✓ Firebase başarıyla bağlandı")
        else:
            print("⚠ Firebase credentials bulunamadı, devam ediliyor...")
            
    except Exception as e:
        print(f"Firebase Hatası: {e}")
        traceback.print_exc()
    
    return db

init_firebase()

# --- HARF TARAMA MOTORU ---
class HarfSistemi:
    """Harf tanıma ve işleme sistemi"""
    
    def __init__(self):
        self.refresh_char_list(3)

    def refresh_char_list(self, variation_count=3):
        """Karakter listesini yenile"""
        self.char_list = []
        for _, key in core_generator.KARAKTER_HARITASI.items():
            for i in range(1, variation_count + 1):
                self.char_list.append(f"{key}_{i}")

    def crop_tight(self, binary_img):
        """Beyaz alanları kırp"""
        coords = cv2.findNonZero(binary_img)
        if coords is None:
            return None
        x, y, w, h = cv2.boundingRect(coords)
        return binary_img[y:y+h, x:x+w]

    def process_roi(self, roi):
        """Tek bir kutucuktaki harfi işle"""
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (3, 3), 0)
        thresh = cv2.adaptiveThreshold(
            blurred, 255, 
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C, 
            cv2.THRESH_BINARY_INV, 25, 12
        )
        
        # Morfolojik işlem
        kernel = np.ones((2, 2), np.uint8)
        opened = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel)
        
        # Kırp
        tight = self.crop_tight(opened)
        if tight is None:
            return None
        
        # Renkli hale getir
        result = np.full((tight.shape[0], tight.shape[1], 3), 255, dtype=np.uint8)
        result[tight == 255] = [0, 0, 0]
        
        return result

    def detect_markers(self, img):
        """Aruco markerları tespit et"""
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        aruco_dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
        
        try:
            # Yeni OpenCV API (4.7+)
            params = cv2.aruco.DetectorParameters()
            detector = cv2.aruco.ArucoDetector(aruco_dict, params)
            corners, ids, _ = detector.detectMarkers(gray)
        except AttributeError:
            # Eski OpenCV API
            corners, ids, _ = cv2.aruco.detectMarkers(gray, aruco_dict)
        
        return corners, ids

    def process_single_page(self, img, section_id):
        """Tek bir sayfa görüntüsünü işle"""
        # Marker tespiti
        corners, ids = self.detect_markers(img)
        
        if ids is None or len(ids) < 4:
            return None, "Marker bulunamadı."
        
        ids = ids.flatten()
        base_id = int(min(ids))
        block_id = int(base_id // 4)
        
        # Perspektif düzeltme
        scale = 10
        margin = 175
        src_width = 2100
        src_height = 1480
        
        # Marker köşelerini sırala
        src_points = []
        for target_id in [block_id * 4, block_id * 4 + 1, block_id * 4 + 2, block_id * 4 + 3]:
            for i in range(len(ids)):
                if ids[i] == target_id:
                    src_points.append(np.mean(corners[i][0], axis=0))
                    break
        
        if len(src_points) < 4:
            return None, "Markerlar eksik."
        
        src_points = np.float32(src_points)
        dst_points = np.float32([
            [margin, margin],
            [src_width - margin, margin],
            [margin, src_height - margin],
            [src_width - margin, src_height - margin]
        ])
        
        # Perspektif dönüşümü
        matrix = cv2.getPerspectiveTransform(src_points, dst_points)
        warped = cv2.warpPerspective(img, matrix, (src_width, src_height))
        
        # Karakterleri çıkar
        page_results = {}
        for row in range(6):
            for col in range(10):
                idx = block_id * 60 + (row * 10 + col)
                if idx >= len(self.char_list):
                    break
                
                # ROI koordinatları
                x1 = 300 + col * 150 + 15
                y1 = 290 + row * 150 + 15
                x2 = x1 + 120
                y2 = y1 + 120
                
                roi = warped[y1:y2, x1:x2]
                processed = self.process_roi(roi)
                
                if processed is not None:
                    _, buffer = cv2.imencode(".png", processed)
                    page_results[self.char_list[idx]] = base64.b64encode(buffer).decode('utf-8')
        
        return {
            'harfler': page_results, 
            'detected': len(page_results), 
            'section_id': block_id
        }, None

sistem = HarfSistemi()

# --- API ENDPOINTS ---

@app.route('/')
def index():
    """Ana sayfa"""
    return jsonify({
        "service": "Fontify API",
        "version": "2.0",
        "status": "running",
        "endpoints": [
            "/health",
            "/api/generate_form",
            "/api/generate_example",
            "/api/get_assets",
            "/process_single",
            "/download"
        ]
    })

@app.route('/health')
def health():
    """Sağlık kontrolü"""
    return "OK", 200

@app.route('/api/generate_form')
def generate_form():
    """Boş form PDF'i oluştur"""
    try:
        variation_count = int(request.args.get('variation_count', 3))
        sistem.refresh_char_list(variation_count)
        
        pdf_buffer = core_generator.FormOlusturucu().tum_formu_olustur(variation_count, False)
        
        if pdf_buffer is None:
            return jsonify({"error": "PDF oluşturulamadı"}), 500
        
        return send_file(
            pdf_buffer, 
            mimetype='application/pdf', 
            as_attachment=True, 
            download_name='form.pdf'
        )
    except Exception as e:
        print(f"Form oluşturma hatası: {e}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route('/api/generate_example')
def generate_example():
    """Örnek dolu form PDF'i oluştur"""
    try:
        variation_count = int(request.args.get('variation_count', 3))
        
        # Örnek harfleri yükle
        assets = core_generator.harf_resimlerini_yukle('static/harfler')
        
        if not assets:
            return jsonify({"error": "Örnek harfler bulunamadı"}), 404
        
        pdf_buffer = core_generator.FormOlusturucu().tum_formu_olustur(
            variation_count, True, assets
        )
        
        if pdf_buffer is None:
            return jsonify({"error": "PDF oluşturulamadı"}), 500
        
        return send_file(
            pdf_buffer, 
            mimetype='application/pdf', 
            as_attachment=True, 
            download_name='ornek.pdf'
        )
    except Exception as e:
        print(f"Örnek oluşturma hatası: {e}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route('/api/get_assets')
def get_assets():
    """Firebase'den font assetlerini getir"""
    font_id = request.args.get('font_id')
    user_id = request.args.get('user_id')
    
    database = init_firebase()
    
    if database and font_id:
        try:
            # Önce genel fonts koleksiyonundan dene
            doc = database.collection('fonts').document(font_id).get()
            
            # Bulunamazsa kullanıcıya özel koleksiyondan dene
            if not doc.exists and user_id:
                doc = database.collection('users').document(user_id).collection('fonts').document(font_id).get()
            
            if doc.exists:
                data = doc.to_dict().get('harfler', {})
                
                # Varyasyonları grupla
                assets = {}
                for key, value in data.items():
                    base = key.rsplit('_', 1)[0]
                    if base not in assets:
                        assets[base] = []
                    assets[base].append(value)
                
                return jsonify({
                    "success": True, 
                    "assets": assets, 
                    "source": "firebase"
                })
        except Exception as e:
            print(f"Asset getirme hatası: {e}")
            traceback.print_exc()
    
    return jsonify({"success": False, "error": "Font bulunamadı"}), 404

@app.route('/process_single', methods=['POST'])
def process_single():
    """Tek sayfa tarama işle"""
    try:
        data = request.get_json()
        
        user_id = data['user_id']
        font_name = data['font_name']
        image_base64 = data['image_base64']
        section_id = data.get('section_id', 0)
        variation_count = data.get('variation_count', 3)
        
        # Karakter listesini güncelle
        sistem.refresh_char_list(variation_count)
        
        # Base64'ü görüntüye çevir
        img_bytes = base64.b64decode(image_base64)
        img_array = np.frombuffer(img_bytes, np.uint8)
        img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        
        # İşle
        result, error = sistem.process_single_page(img, section_id)
        
        if error:
            return jsonify({'success': False, 'message': error}), 400
        
        # Firebase'e kaydet
        database = init_firebase()
        if database:
            try:
                font_id = f"{user_id}_{font_name.replace(' ', '_')}"
                
                # Ana fonts koleksiyonu
                ref = database.collection('fonts').document(font_id)
                # Kullanıcıya özel koleksiyon
                user_ref = database.collection('users').document(user_id).collection('fonts').document(font_id)
                
                # Döküman var mı kontrol et
                doc = ref.get()
                
                if not doc.exists:
                    # Yeni döküman oluştur
                    payload = {
                        'harfler': result['harfler'],
                        'owner_id': user_id,
                        'font_name': font_name,
                        'font_id': font_id,
                        'variation_count': variation_count,
                        'created_at': firestore.SERVER_TIMESTAMP
                    }
                    ref.set(payload)
                    user_ref.set(payload)
                else:
                    # Mevcut harfleri güncelle
                    existing_harfler = doc.to_dict().get('harfler', {})
                    existing_harfler.update(result['harfler'])
                    ref.update({'harfler': existing_harfler})
                    user_ref.update({'harfler': existing_harfler})
                
                # Tarama durumunu kaydet
                database.collection('temp_scans').document(font_id).set({
                    f'section_{result["section_id"]}': True,
                    'last_update': firestore.SERVER_TIMESTAMP
                }, merge=True)
                
            except Exception as e:
                print(f"Firebase kayıt hatası: {e}")
                traceback.print_exc()
        
        return jsonify({
            'success': True, 
            'detected_chars': len(result['harfler']),
            'section_id': result['section_id']
        })
        
    except Exception as e:
        print(f"İşleme hatası: {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/download', methods=['POST'])
def download():
    """El yazısı PDF indir"""
    try:
        font_id = request.form.get('font_id')
        user_id = request.form.get('user_id')
        metin = request.form.get('metin', '')
        
        # Parametreler
        yazi_boyutu = int(request.form.get('yazi_boyutu', 140))
        satir_araligi = int(request.form.get('satir_araligi', 220))
        kelime_boslugu = int(request.form.get('kelime_boslugu', 55))
        jitter = int(request.form.get('jitter', 3))
        kalinlik = int(float(request.form.get('kalinlik', 0)))
        paper_type = request.form.get('paper_type', 'duz')
        
        # Harfleri yükle
        active_harfler = {}
        
        db = init_firebase()
        if db and font_id:
            try:
                doc = db.collection('fonts').document(font_id).get()
                
                if doc.exists:
                    for key, value in doc.to_dict().get('harfler', {}).items():
                        base = key.rsplit('_', 1)[0]
                        if base not in active_harfler:
                            active_harfler[base] = []
                        
                        # Base64'ü Image'e çevir
                        img_bytes = base64.b64decode(value)
                        img = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
                        active_harfler[base].append(img)
            except Exception as e:
                print(f"Harf yükleme hatası: {e}")
                traceback.print_exc()
        
        # Fallback: örnek harfler
        if not active_harfler:
            active_harfler = core_generator.harf_resimlerini_yukle('static/harfler')
        
        if not active_harfler:
            return jsonify({"error": "Harf bulunamadı"}), 404
        
        # Konfigürasyon
        config = {
            'page_width': 2480,
            'page_height': 3508,
            'margin_top': 200,
            'margin_left': 150,
            'margin_right': 150,
            'target_letter_height': yazi_boyutu,
            'line_spacing': satir_araligi,
            'word_spacing': kelime_boslugu,
            'jitter': jitter,
            'paper_type': paper_type,
            'murekkep_rengi': (27, 27, 29),
            'kalinlik': kalinlik
        }
        
        # Sayfaları oluştur
        sayfalar = core_generator.metni_sayfaya_yaz(metin, active_harfler, config)
        
        # PDF oluştur
        pdf_buffer = core_generator.sayfalari_pdf_olustur(sayfalar)
        
        if pdf_buffer is None:
            return jsonify({"error": "PDF oluşturulamadı"}), 500
        
        return send_file(
            pdf_buffer, 
            mimetype='application/pdf', 
            as_attachment=True, 
            download_name='yazi.pdf'
        )
        
    except Exception as e:
        print(f"Download hatası: {e}")
        traceback.print_exc()
        return str(e), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)
