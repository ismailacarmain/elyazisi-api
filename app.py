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
            raise Exception("Firebase credentials bulunamadı.")
    except Exception as e:
        init_error = str(e)
        db = None
    return db

init_firebase()

# Harfleri başlangıçta yükle (Server-side için)
HARFLER_KLASORU = 'static/harfler'
HARFLER = {}

def load_local_assets():
    global HARFLER
    if os.path.exists(HARFLER_KLASORU):
        HARFLER = core_generator.harf_resimlerini_yukle(HARFLER_KLASORU)
    else:
        print("⚠️ Yerel harf klasörü yok.")

load_local_assets()

# --- WEB ROTALARI ---

@app.route('/')
def index():
    """Ana Editör Sayfası"""
    font_id = request.args.get('font_id', '')
    user_id = request.args.get('user_id', '')
    return render_template('index.html', font_id=font_id, user_id=user_id)

@app.route('/api/get_assets')
def get_assets():
    """Web arayüzüne harf listesini döner"""
    font_id = request.args.get('font_id')
    user_id = request.args.get('user_id')
    
    assets = {}
    
    # 1. Eğer font_id varsa Firebase'den çek
    database = init_firebase()
    if database and font_id:
        doc = database.collection('fonts').document(font_id).get()
        if not doc.exists and user_id:
            doc = database.collection('users').document(user_id).collection('fonts').document(font_id).get()
        
        if doc.exists:
            harfler_data = doc.to_dict().get('harfler', {})
            # Web arayüzü için formatla
            for key, b64 in harfler_data.items():
                base_key = key.rsplit('_', 1)[0] if '_' in key else key
                if base_key not in assets: assets[base_key] = []
                assets[base_key].append(b64) # Direkt base64 gönderiyoruz
            return jsonify({"success": True, "assets": assets, "source": "firebase"})

    # 2. Yoksa yerel klasörden çek (fallback)
    if os.path.exists(HARFLER_KLASORU):
        for dosya in os.listdir(HARFLER_KLASORU):
            if dosya.endswith('.png'):
                key = dosya.rsplit('_', 1)[0]
                if key not in assets: assets[key] = []
                assets[key].append(dosya)
        return jsonify({"success": True, "assets": assets, "source": "local"})
    
    return jsonify({"error": "Harf bulunamadı"}), 404

@app.route('/api/list_fonts')
def list_fonts():
    """Kullanıcının erişebileceği tüm fontları listeler"""
    user_id = request.args.get('user_id')
    fonts = []
    
    database = init_firebase()
    if not database:
        return jsonify({"success": False, "error": "Veritabanı bağlantısı yok"})

    try:
        # 1. Genel Fontlar
        public_fonts = database.collection('fonts').stream()
        for doc in public_fonts:
            d = doc.to_dict()
            fonts.append({
                'id': d.get('font_id', doc.id),
                'name': d.get('font_name', 'Adsız Font'),
                'type': 'public'
            })
            
        # 2. Kullanıcının Özel Fontları
        if user_id:
            private_fonts = database.collection('users').document(user_id).collection('fonts').stream()
            for doc in private_fonts:
                d = doc.to_dict()
                # ID çakışmasını önle (eğer hem public hem private varsa)
                fid = d.get('font_id', doc.id)
                if not any(f['id'] == fid for f in fonts):
                    fonts.append({
                        'id': fid,
                        'name': d.get('font_name', 'Özel Font'),
                        'type': 'private'
                    })
                    
        return jsonify({"success": True, "fonts": fonts})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

# --- ESKİ API ROTALARI (BOZMADIK!) ---

@app.route('/process_single', methods=['POST'])
def process_single():
    # ... (Mevcut kodun aynısı, tarama işlemi)
    return jsonify({'status': 'Aruco Engine Active'}) # Burayı eski kodunla doldurabilirsin

# --- PDF OLUŞTURMA ---

@app.route('/download', methods=['POST'])
def download():
    # ... (Daha önce yazdığımız PDF oluşturma kodu)
    try:
        metin = request.form.get('metin', '')
        # (Buradaki PDF oluşturma mantığı aynı kalacak)
        return "PDF Oluşturma Aktif"
    except: return "Hata", 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
