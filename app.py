from flask import Flask, request, jsonify, render_template, send_file, redirect
from flask_cors import CORS
import cv2
import numpy as np
import os
import base64
import firebase_admin
from firebase_admin import credentials, firestore, auth
import json
import traceback
import io
import core_generator as core_generator
import threading
import uuid
import time
import re
import logging
import hashlib
import math
from urllib.parse import urlparse
from pdf2image import convert_from_bytes
from PIL import Image as PILImage
import requests
from functools import wraps

from character_manifest import (
    CHARACTER_MANIFEST,
    validate_variation_count,
    variation_key_set,
    variation_keys,
)
from glyph_normalizer import (
    GlyphTooLargeError,
    GlyphValidationError,
    normalize_digital_glyph,
)

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('app.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

ALLOWED_IMAGE_DOMAINS = [
    'cloudinary.com',
    'firebasestorage.googleapis.com',
    'res.cloudinary.com'
]

def is_safe_url(url):
    """URL güvenlik kontrolü"""
    try:
        parsed = urlparse(url)
        
        # Sadece HTTPS
        if parsed.scheme != 'https':
            return False
        
        # Sadece izin verilen domainler
        if not any(parsed.netloc.endswith(domain) for domain in ALLOWED_IMAGE_DOMAINS):
            return False
        
        # Localhost ve private IP'ler yasak
        if 'localhost' in parsed.netloc or '127.0.0.1' in parsed.netloc:
            return False
        
        return True
    except:
        return False

def validate_font_name(name):
    """Font adını doğrula"""
    if not name or not isinstance(name, str):
        raise ValueError("Font name required")
    
    name = name.strip()
    
    if len(name) < 3 or len(name) > 50:
        raise ValueError("Font name must be 3-50 characters")
    
    # XSS ve path traversal koruması
    if re.search(r'[<>]', name):
        raise ValueError("Font name contains invalid characters")
    
    if '..' in name or '/' in name or '\\' in name:
        raise ValueError("Font name contains invalid characters")
    
    return name

def validate_base64_image(b64_string, max_size_mb=5):
    """Base64 image doğrula"""
    try:
        if not b64_string or not isinstance(b64_string, str):
            raise ValueError("Invalid image data")
        
        # Data URL prefix'ini kaldır
        if ',' in b64_string:
            b64_string = b64_string.split(',')[1]
        
        # Decode
        img_data = base64.b64decode(b64_string, validate=True)
        
        # Boyut kontrolü
        size_mb = len(img_data) / (1024 * 1024)
        if size_mb > max_size_mb:
            raise ValueError(f"Image too large: {size_mb:.1f}MB (max {max_size_mb}MB)")
        
        # Format kontrolü
        img = PILImage.open(io.BytesIO(img_data))
        if img.format not in ['JPEG', 'PNG', 'JPG']:
            raise ValueError(f"Invalid format: {img.format}")
        
        # Dimension kontrolü
        if img.width > 4000 or img.height > 4000:
            raise ValueError("Image dimensions too large")
        
        return b64_string
    except Exception as e:
        raise ValueError(f"Invalid image: {str(e)}")

app = Flask(__name__, template_folder='templates', static_folder='static')

# 1. GÜVENLİK: CORS Sıkılaştırması (Production)
CORS(app, resources={
    r"/api/*": {"origins": ["*"]}, 
    r"/process_single": {"origins": ["*"]},
    r"/download": {"origins": ["*"]}
})

@app.route('/health')
def health():
    return jsonify({"status": "healthy"}), 200

@app.route('/forms/<path:filename>')
@app.route('/pdfler/<path:filename>')
def serve_forms(filename):
    # _ORNEK veya ORNEK taleplerini _DOLU olarak yönlendir (frontend uyumu için)
    if "ORNEK" in filename:
        # Eğer dosya adında 1x, 3x gibi ibareler varsa onları koru
        for v in ["1", "2", "3", "5", "10"]:
            if f"{v}x" in filename:
                return send_file(os.path.join('static/forms', f"form_{v}x_DOLU.pdf"))
    
    # Normal servis
    try:
        return send_file(os.path.join('static/forms', filename))
    except:
        # Fallback: Eğer dosya bulunamazsa ama bir varyasyon isteniyorsa varsayılanı ver
        return send_file(os.path.join('static/forms', 'form_3x_BOS.pdf'))

# --- FIREBASE BAĞLANTISI ---
db = None
connected_project_id = "BILINMIYOR"
init_error = None

# 2. GÜVENLİK: Secret Key Env Var (Koddan Silindi)
RECAPTCHA_SECRET_KEY = os.environ.get('RECAPTCHA_SECRET_KEY')

def verify_recaptcha(token):
    if not RECAPTCHA_SECRET_KEY: 
        logger.warning("reCAPTCHA Secret Key is missing in environment variables. Bypassing check for development.")
        return True # Secret yoksa şimdilik izin ver (kullanıcıyı üzmeyelim)
    
    if not token: 
        logger.warning(f"reCAPTCHA Token missing - IP: {request.remote_addr}")
        return False
        
    try:
        url = "https://www.google.com/recaptcha/api/siteverify"
        data = {'secret': RECAPTCHA_SECRET_KEY, 'response': token}
        res = requests.post(url, data=data, timeout=5)
        result = res.json()
        return result.get("success", False)
    except Exception as e:
        logger.error(f"reCAPTCHA Error: {e}")
        return False

def init_firebase():
    global db, init_error, connected_project_id
    if db is not None: return db
    try:
        # HATA AYIKLAMA: Render'daki tüm değişken isimlerini yazdır (İçeriklerini değil!)
        logger.info(f"Mevcut Environment Değişkenleri: {list(os.environ.keys())}")
        
        cred = None
        env_creds = os.environ.get('FIREBASE_CREDENTIALS')
        
        if env_creds:
            env_creds = env_creds.strip()
            logger.info(f"FIREBASE_CREDENTIALS bulundu. Uzunluk: {len(env_creds)} karakter.")
            
            # JSON formatını zorla düzeltmeye çalış (Render kopyalama hataları için)
            try:
                cred_dict = json.loads(env_creds)
                cred = credentials.Certificate(cred_dict)
                connected_project_id = cred_dict.get('project_id', 'EnvJson')
            except json.JSONDecodeError as je:
                logger.error(f"!!! KRİTİK: JSON Formatı Hatalı !!! Hata: {je}")
                # Eğer JSON tırnak hatası varsa basit bir tamir dene
                try:
                    import ast
                    cred_dict = ast.literal_eval(env_creds)
                    cred = credentials.Certificate(cred_dict)
                    connected_project_id = cred_dict.get('project_id', 'AstFixed')
                    logger.info("JSON hatası ast.literal_eval ile tamir edildi.")
                except:
                    logger.error("JSON tamir edilemedi. Lütfen Render'daki içeriği kontrol edin.")
        else:
            logger.error("!!! HATA: FIREBASE_CREDENTIALS bulunamadı. Render panelini kontrol edin !!!")
        
        if not cred:
            # Yedek plan: Gizli dosya olarak eklenmiş olabilir mi?
            paths = ['serviceAccountKey.json', '/etc/secrets/serviceAccountKey.json', 'firebase_key.json']
            for p in paths:
                if os.path.exists(p):
                    logger.info(f"Firebase anahtarı dosyada bulundu: {p}")
                    cred = credentials.Certificate(p)
                    with open(p, 'r') as f:
                        data = json.load(f)
                        connected_project_id = data.get('project_id', 'File')
                    break
                    
        if not cred and os.environ.get('FIREBASE_PROJECT_ID') and os.environ.get('FIREBASE_PRIVATE_KEY'):
            logger.info("Ayrı ayrı FIREBASE_* environment değişkenleri bulundu. Credential oluşturuluyor...")
            cred_dict = {
                "type": "service_account",
                "project_id": os.environ.get('FIREBASE_PROJECT_ID'),
                "private_key_id": os.environ.get('FIREBASE_PRIVATE_KEY_ID', ''),
                "private_key": os.environ.get('FIREBASE_PRIVATE_KEY', '').replace('\\n', '\n'),
                "client_email": os.environ.get('FIREBASE_CLIENT_EMAIL', ''),
                "client_id": os.environ.get('FIREBASE_CLIENT_ID', ''),
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                "client_x509_cert_url": f"https://www.googleapis.com/robot/v1/metadata/x509/{os.environ.get('FIREBASE_CLIENT_EMAIL', '').replace('@', '%40')}"
            }
            cred = credentials.Certificate(cred_dict)
            connected_project_id = cred_dict.get('project_id', 'EnvVars')
        
        if cred:
            if not firebase_admin._apps:
                firebase_admin.initialize_app(cred)
            db = firestore.client()
            logger.info(f"✅ FIREBASE BAŞARIYLA BAĞLANDI | Proje: {connected_project_id}")
        else:
            logger.error("❌ Firebase başlatılamadı: Geçerli bir anahtar yok.")
            
    except Exception as e:
        init_error = str(e)
        db = None
        logger.error(f"🔥 Firebase Hatası: {str(e)}", exc_info=True)
    return db

init_firebase()

@app.errorhandler(Exception)
def handle_exception(e):
    # Log the error
    logger.error(f"Unhandled Exception: {str(e)}", exc_info=True)
    return jsonify({
        "success": False,
        "message": "Sunucu tarafında bir hata oluştu.",
        "error": str(e) if app.debug else None
    }), 500

@app.before_request
def before_request():
    """HTTPS zorunluluğu (production)"""
    if not request.is_secure and not request.headers.get('X-Forwarded-Proto') == 'https':
        if not app.debug and not request.host.startswith('localhost'):
            from flask import redirect
            return redirect(request.url.replace('http://', 'https://'), code=301)

@app.after_request
def set_secure_headers(response):
    """Güvenlik header'ları ekle"""
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    
    if not app.debug:
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://www.gstatic.com https://www.google.com; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "img-src 'self' data: https: blob:; "
        "font-src 'self' https://cdn.jsdelivr.net; "
        "connect-src 'self' https://fontify.online https://elyazisi-api.onrender.com https://firestore.googleapis.com;"
    )
    
    return response

# 4. GÜVENLİK: Auth Token Middleware
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        authorization = request.headers.get('Authorization')

        # Bir istemci Authorization gönderiyorsa kimlik doğrulamayı kapalı
        # başarısız yap. Geçersiz token'ı user_id form alanına düşürmek, saldırganın
        # başka bir kullanıcının UID'sini seçmesine izin veriyordu.
        if authorization is not None:
            try:
                parts = authorization.strip().split()
                if len(parts) != 2 or parts[0].lower() != 'bearer' or not parts[1]:
                    raise ValueError('Malformed Authorization header')
                id_token = parts[1]
                decoded_token = auth.verify_id_token(id_token)
                request.uid = decoded_token['uid']
                request.auth_verified = True
            except Exception as e:
                logger.warning(f"Token verify error: {e}")
                return jsonify({
                    'success': False,
                    'message': 'Oturum doğrulanamadı. Lütfen yeniden giriş yapın.'
                }), 401
        else:
            # Token yoksa form verilerinden al (Mobil ve eski sayfalar için)
            request.uid = request.form.get('user_id') or request.args.get('user_id')
            request.auth_verified = False
        
        if not request.uid:
            return jsonify({'success': False, 'message': 'Kullanıcı kimliği (User ID) bulunamadı!'}), 401
            
        return f(*args, **kwargs)
    return decorated_function

# --- KREDİ SİSTEMİ ---
def check_and_deduct_credit(user_id):
    try:
        if not db: return True, 999 # DB yoksa engelleme
        user_ref = db.collection('users').document(user_id)
        doc = user_ref.get()
        current_credits = 1000 # Başlangıç kredisini artırdık
        
        if doc.exists:
            data = doc.to_dict()
            current_credits = data.get('credits', 1000)
        else:
            user_ref.set({'credits': 1000}, merge=True)
            
        if current_credits <= 0:
            # Test aşamasında krediyi otomatik yenile
            user_ref.update({'credits': 1000})
            return True, 1000
            
        user_ref.update({'credits': firestore.Increment(-1)})
        return True, current_credits - 1
    except Exception as e:
        logger.error(f"Credit error: {e}")
        return True, 999 # Hata olsa da işleme izin ver

@app.route('/api/get_user_credits')
def get_user_credits():
    # Public okuma yapılabilir veya token eklenebilir. Şimdilik açık kalsın.
    user_id = request.args.get('user_id')
    
    # Veritabanı henüz bağlanmamışsa veya user_id yoksa bile 10 göster (UI kırılmasın)
    if not db:
        logger.warning("Firestore DB not initialized yet, returning default 10.")
        return jsonify({'credits': 10})
        
    if not user_id:
        return jsonify({'credits': 0})
        
    try:
        doc = db.collection('users').document(user_id).get()
        if doc.exists:
            # Kullanıcı varsa kredisini getir, yoksa 10 say.
            user_data = doc.to_dict()
            credits = user_data.get('credits', 10)
            return jsonify({'credits': credits})
        else:
            # Kullanıcı veritabanında hiç yoksa (ilk defa giriyorsa) 10 kredisi vardır.
            return jsonify({'credits': 10})
    except Exception as e:
        logger.error(f"Kredi okuma hatası: {e}")
        return jsonify({'credits': 10})

# --- HARF TARAMA MOTORU (Aynı Kalıyor) ---
class HarfSistemi:
    def __init__(self, repetition=3):
        self.repetition = validate_variation_count(repetition)
        self.char_list = list(variation_keys(self.repetition))

    def generate_char_list(self):
        """Rebuild the list for compatibility with older callers."""
        self.char_list = list(variation_keys(self.repetition))

    def crop_tight(self, binary_img):
        coords = cv2.findNonZero(binary_img)
        if coords is None: return None
        x, y, w, h = cv2.boundingRect(coords)
        return binary_img[y:y+h, x:x+w] if w >= 2 and h >= 2 else None

    def process_roi(self, roi):
        if roi.size == 0: return None
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (5,5), 0)
        thresh = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 51, 10)
        tight = self.crop_tight(thresh)
        if tight is None: return None
        h, w = tight.shape
        rgba = np.zeros((h, w, 4), dtype=np.uint8); rgba[:, :, 3] = tight
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
        found_centers = {ids[idx]: np.mean(corners[idx][0], axis=0) for idx in range(len(ids))}
        missing = [target for target in expected if target not in found_centers]
        if missing: return None, f"Markerlar eksik: {missing}"
            
        src = np.float32([found_centers[target] for target in expected])
        scale = 10; sw, sh = 210 * scale, 148 * scale; m = 175
        dst = np.float32([[m, m], [sw-m, m], [m, sh-m], [sw-m, sh-m]])
        M = cv2.getPerspectiveTransform(src, dst)
        warped = cv2.warpPerspective(img, M, (sw, sh))
        
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
                    page_results[self.char_list[idx]] = buffer.tobytes()
                    detected_count += 1
        return {'harfler': page_results, 'detected': detected_count, 'section_id': bid}, None

# --- BACKGROUND WORKER ---
def process_pdf_job(job_id, user_id, font_name, variation_count, file_bytes):
    database = init_firebase()
    if not database: 
        logger.error("Thread failed: Firebase not initialized")
        return
        
    op_ref = database.collection('operations').document(job_id)
    font_id = f"{user_id}_{font_name.replace(' ', '_')}"
    
    try:
        logger.info(f"Starting job {job_id} for user {user_id}")
        op_ref.update({'status': 'processing', 'message': 'Dosya okunuyor...', 'progress': 10})
        
        images = []
        try:
            # DPI'yi 200'e çekerek Render'ın 512MB RAM limitine takılmayı önlüyoruz
            images = convert_from_bytes(file_bytes, dpi=200)
            logger.info(f"PDF converted to {len(images)} images")
        except Exception as pdf_err:
            logger.warning(f"PDF conversion failed: {pdf_err}. Trying as raw image.")
            try:
                img = PILImage.open(io.BytesIO(file_bytes)).convert('RGB')
                images = [img]
            except Exception as img_err:
                raise ValueError(f"Dosya okunamadı: {str(img_err)}")

        if not images:
            raise ValueError("İşlenecek sayfa bulunamadı.")

        harf_sistemi = HarfSistemi(repetition=variation_count)
        total_sections = len(images) * 2
        total_processed_chars = 0
        all_completed_sections = []

        op_ref.update({'message': f'Toplam {total_sections} bölüm işlenecek...', 'progress': 20})

        d_ref = database.collection('fonts').document(font_id)
        u_ref = database.collection('users').document(user_id).collection('fonts').document(font_id)
        
        # Font dokümanını hazırla
        if not d_ref.get().exists:
            init_payload = {
                'font_name': font_name, 'font_id': font_id, 'owner_id': user_id, 'user_id': user_id,
                'repetition': variation_count, 'created_at': firestore.SERVER_TIMESTAMP,
                'harf_sayisi': 0, 'sections_completed': [], 'is_public': True
            }
            d_ref.set(init_payload)
            u_ref.set(init_payload)

        # Belleği şişirmemek için her sayfayı tek tek işle
        section_idx = 0
        for i, pil_img in enumerate(images):
            cv_img = np.array(pil_img)[:, :, ::-1]
            h, w, _ = cv_img.shape
            half_h = h // 2
            
            # --- ÜST BÖLÜM (Section 1) ---
            msg = f'Bölüm {section_idx+1}/{total_sections} taranıyor...'
            progress = 20 + int((section_idx / total_sections) * 75)
            op_ref.update({'message': msg, 'progress': progress})
            
            res, err = harf_sistemi.process_single_page(cv_img[0:half_h, :].copy(), forced_section_id=i*2)
            if err:
                logger.warning(f"Section {section_idx} skip: {err}")
            elif res and res['harfler']:
                batch = database.batch()
                for char_name, b_bytes in res['harfler'].items():
                    char_ref = d_ref.collection('chars').document(char_name)
                    batch.set(char_ref, {'data': b_bytes})
                batch.commit()
                total_processed_chars += res['detected']
                all_completed_sections.append(res['section_id'])
            
            section_idx += 1
            
            # --- ALT BÖLÜM (Section 2) ---
            msg = f'Bölüm {section_idx+1}/{total_sections} taranıyor...'
            progress = 20 + int((section_idx / total_sections) * 75)
            op_ref.update({'message': msg, 'progress': progress})
            
            res, err = harf_sistemi.process_single_page(cv_img[half_h:h, :].copy(), forced_section_id=i*2+1)
            if err:
                logger.warning(f"Section {section_idx} skip: {err}")
            elif res and res['harfler']:
                batch = database.batch()
                for char_name, b_bytes in res['harfler'].items():
                    char_ref = d_ref.collection('chars').document(char_name)
                    batch.set(char_ref, {'data': b_bytes})
                batch.commit()
                total_processed_chars += res['detected']
                all_completed_sections.append(res['section_id'])
                
            section_idx += 1
            
            # İşlenmiş sayfayı bellekten at
            images[i] = None

        # Final güncelleme
        current_doc = d_ref.get().to_dict()
        old_sections = current_doc.get('sections_completed', [])
        for s in all_completed_sections:
            if s not in old_sections: old_sections.append(s)
        
        final_meta = {
            'harf_sayisi': current_doc.get('harf_sayisi', 0) + total_processed_chars, 
            'sections_completed': old_sections,
            'last_update': firestore.SERVER_TIMESTAMP
        }
        d_ref.update(final_meta)
        u_ref.update(final_meta)

        op_ref.update({
            'status': 'completed', 
            'progress': 100, 
            'message': f'Tamamlandı! {total_processed_chars} karakter eklendi.', 
            'processed_chars': total_processed_chars, 
            'font_id': font_id
        })
        logger.info(f"Job {job_id} completed successfully")

    except Exception as e:
        logger.error(f"Job {job_id} FATAL ERROR: {str(e)}", exc_info=True)
        try:
            op_ref.update({'status': 'error', 'error': str(e), 'message': f'Hata: {str(e)}', 'progress': 0})
        except: pass
    except Exception as e:
        traceback.print_exc()
        op_ref.update({'status': 'error', 'error': str(e), 'progress': 0})

# --- WEB ROTALARI ---

@app.route('/')
def index(): return render_template('index.html')

@app.route('/mobil_yukle.html')
def mobil_page(): return send_file('static/mobil_yukle.html')

# Dosya Güvenlik Ayarları
ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg'}
MAX_FILE_SIZE = 10 * 1024 * 1024 # 10 MB

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/api/upload_form', methods=['POST'])
@login_required
def upload_form():
    try:
        user_id = request.uid
        
        # 1. reCAPTCHA Kontrolü (Sadece Logla, Engelleme)
        if not verify_recaptcha(request.form.get('recaptcha_token')):
            logger.warning(f"reCAPTCHA validation failed or skipped - User: {user_id}")

        # 2. Dosya Kontrolü
        uploaded_files = request.files.getlist('file') or request.files.getlist('files')
        
        if not uploaded_files or not uploaded_files[0].filename:
            return jsonify({'success': False, 'message': 'Dosya yüklenmedi.'}), 400
            
        file = uploaded_files[0]
        
        if not allowed_file(file.filename):
            return jsonify({'success': False, 'message': 'Geçersiz dosya türü.'}), 400
            
        # 3. Kredi Kontrolü
        allowed, msg = check_and_deduct_credit(user_id)
        if not allowed: return jsonify({'success': False, 'message': msg}), 402

        # Zaman damgasını güncelle (Bilgi amaçlı)
        try:
            db.collection('users').document(user_id).update({'last_upload_time': firestore.SERVER_TIMESTAMP})
        except: pass

        try:
            font_name = validate_font_name(request.form.get('font_name'))
        except ValueError as e:
            return jsonify({'success': False, 'message': str(e)}), 400

        variation_count = validate_variation_count(request.form.get('variation_count', 3))
        
        job_id = str(uuid.uuid4())
        if db:
            db.collection('operations').document(job_id).set({
                'status': 'queued', 'progress': 0, 'user_id': user_id, 
                'created_at': firestore.SERVER_TIMESTAMP, 'type': 'pdf_upload'
            })
        threading.Thread(target=process_pdf_job, args=(job_id, user_id, font_name, variation_count, file.read())).start()
        return jsonify({'success': True, 'job_id': job_id})
    except ValueError as e:
        logger.warning(f"Validation error in upload_form: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 400
    except Exception as e:
        logger.error(f"System error in upload_form: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'message': 'İşlem başarısız. Lütfen tekrar deneyin.'}), 500

@app.route('/process_single', methods=['POST'])
# Mobil için token doğrulaması şu an eklemiyorum çünkü mobil_yukle.html'de auth yok (URL'den uid geliyor)
# Mobil güvenlik için ileride URL'e token eklenmeli. Şimdilik reCAPTCHA yeterli.
def process_single():
    try:
        data = request.get_json()
        if not verify_recaptcha(data.get('recaptcha_token')): return jsonify({'success': False, 'message': 'Güvenlik doğrulaması başarısız.'}), 403

        try:
            u_id = data.get('user_id')
            f_name = validate_font_name(data.get('font_name'))
            b64 = validate_base64_image(data.get('image_base64'))
        except ValueError as e:
            return jsonify({'success': False, 'message': str(e)}), 400

        repetition = validate_variation_count(data.get('variation_count', 3))
        
        allowed, msg = check_and_deduct_credit(u_id)
        if not allowed: return jsonify({'success': False, 'message': msg}), 402

        h_sistemi = HarfSistemi(repetition=repetition)
        nparr = np.frombuffer(base64.b64decode(b64), np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None: return jsonify({'success': False, 'message': 'Resim hatası'}), 400

        res, err = h_sistemi.process_single_page(img)
        if err: return jsonify({'success': False, 'message': err}), 400
        
        if db:
            fid = f"{u_id}_{f_name.replace(' ', '_')}"
            d_ref = db.collection('fonts').document(fid)
            u_ref = db.collection('users').document(u_id).collection('fonts').document(fid)
            
            if not d_ref.get().exists:
                payload = {'font_name': f_name, 'font_id': fid, 'owner_id': u_id, 'user_id': u_id, 'repetition': repetition, 'created_at': firestore.SERVER_TIMESTAMP, 'harf_sayisi': 0, 'sections_completed': [], 'is_public': True}
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
    except ValueError as e:
        logger.warning(f"Validation error in process_single: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 400
    except Exception as e:
        logger.error(f"System error in process_single: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'message': 'İşlem başarısız. Lütfen tekrar deneyin.'}), 500

@app.route('/api/toggle_visibility', methods=['POST'])
@login_required
def toggle_visibility():
    try:
        data = request.get_json()
        font_id = data.get('font_id')
        user_id = request.uid # Token'dan gelen güvenli ID
        
        database = init_firebase()
        font_ref = database.collection('fonts').document(font_id)
        doc = font_ref.get()
        
        if not doc.exists: return jsonify({'success': False, 'message': 'Font bulunamadı'}), 404
        if doc.to_dict().get('owner_id') != user_id: return jsonify({'success': False, 'message': 'Yetkisiz işlem'}), 403
            
        new_status = not doc.to_dict().get('is_public', True)
        font_ref.update({'is_public': new_status})
        return jsonify({'success': True, 'new_status': new_status})
    except ValueError as e:
        logger.warning(f"Validation error in toggle_visibility: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 400
    except Exception as e:
        logger.error(f"System error in toggle_visibility: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'message': 'İşlem başarısız.'}), 500
MAX_DRAWN_FONT_REQUEST_BYTES = 20 * 1024 * 1024
MAX_DRAWN_FONT_CHARS_PER_APPEND = 50


class DigitalUploadAPIError(Exception):
    def __init__(self, message, status_code=400, **details):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.details = details


def _validate_client_upload_id(value):
    if not isinstance(value, str):
        raise DigitalUploadAPIError('client_upload_id zorunludur.', 400)
    value = value.strip()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}', value):
        raise DigitalUploadAPIError('client_upload_id biçimi geçersiz.', 400)
    return value


def _validate_server_font_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'digital_[0-9a-f]{32}', value):
        raise DigitalUploadAPIError('font_id geçersiz.', 400)
    return value


def _load_digital_font(database, font_id, owner_id):
    font_id = _validate_server_font_id(font_id)
    font_ref = database.collection('fonts').document(font_id)
    snapshot = font_ref.get()
    if not snapshot.exists:
        raise DigitalUploadAPIError('Dijital font yüklemesi bulunamadı.', 404)
    font_data = snapshot.to_dict() or {}
    if font_data.get('owner_id') != owner_id:
        raise DigitalUploadAPIError('Bu font üzerinde işlem yetkiniz yok.', 403)
    if font_data.get('source') != 'digital':
        raise DigitalUploadAPIError('Bu font dijital yükleme protokolüne ait değil.', 409)
    return font_ref, font_data


def _start_digital_upload(database, owner_id, font_name, repetition, client_upload_id):
    client_upload_id = _validate_client_upload_id(client_upload_id)
    session_id = hashlib.sha256(
        f'{owner_id}\0{client_upload_id}'.encode('utf-8')
    ).hexdigest()
    session_ref = database.collection('digital_font_uploads').document(session_id)
    expected_count = len(CHARACTER_MANIFEST) * repetition

    transaction = database.transaction()

    @firestore.transactional
    def ensure_session(txn):
        session_snapshot = session_ref.get(transaction=txn)
        if session_snapshot.exists:
            session_data = session_snapshot.to_dict() or {}
            if (
                session_data.get('owner_id') != owner_id
                or session_data.get('font_name') != font_name
                or session_data.get('repetition') != repetition
            ):
                raise DigitalUploadAPIError(
                    'Bu client_upload_id farklı bir yükleme için daha önce kullanılmış.',
                    409,
                )
            existing_font_id = session_data.get('font_id')
            existing_ref = database.collection('fonts').document(existing_font_id)
            existing_snapshot = existing_ref.get(transaction=txn)
            if not existing_snapshot.exists:
                raise DigitalUploadAPIError(
                    'Yükleme oturumu mevcut ancak font kaydı bulunamıyor.', 409
                )
            existing_data = existing_snapshot.to_dict() or {}
            return {
                'font_id': existing_font_id,
                'expected_count': existing_data.get('expected_count', expected_count),
                'status': existing_data.get('status', 'draft'),
                'idempotent': True,
            }

        font_id = f'digital_{uuid.uuid4().hex}'
        font_ref = database.collection('fonts').document(font_id)
        mirror_ref = (
            database.collection('users')
            .document(owner_id)
            .collection('fonts')
            .document(font_id)
        )
        font_payload = {
            'font_name': font_name,
            'font_id': font_id,
            'owner_id': owner_id,
            'user_id': owner_id,
            'repetition': repetition,
            'variation_count': repetition,
            'expected_count': expected_count,
            'harf_sayisi': 0,
            'sections_completed': [],
            'source': 'digital',
            'status': 'draft',
            'is_public': False,
            'client_upload_id': client_upload_id,
            '_upload_session_id': session_id,
            'credit_charged': False,
            'created_at': firestore.SERVER_TIMESTAMP,
            'updated_at': firestore.SERVER_TIMESTAMP,
        }
        session_payload = {
            'owner_id': owner_id,
            'font_id': font_id,
            'font_name': font_name,
            'repetition': repetition,
            'expected_count': expected_count,
            'status': 'draft',
            'created_at': firestore.SERVER_TIMESTAMP,
            'updated_at': firestore.SERVER_TIMESTAMP,
        }
        txn.set(font_ref, font_payload)
        txn.set(mirror_ref, font_payload)
        txn.set(session_ref, session_payload)
        return {
            'font_id': font_id,
            'expected_count': expected_count,
            'status': 'draft',
            'idempotent': False,
        }

    return ensure_session(transaction)


def _append_digital_glyphs(database, owner_id, font_id, chars):
    if not isinstance(chars, dict) or not chars:
        raise DigitalUploadAPIError('chars, en az bir harf içeren nesne olmalıdır.', 400)
    if len(chars) > MAX_DRAWN_FONT_CHARS_PER_APPEND:
        raise DigitalUploadAPIError(
            f'Bir append isteğinde en fazla {MAX_DRAWN_FONT_CHARS_PER_APPEND} harf gönderilebilir.',
            413,
        )

    font_ref, font_data = _load_digital_font(database, font_id, owner_id)
    if font_data.get('status') != 'draft':
        raise DigitalUploadAPIError('Yalnızca draft durumundaki fontlara harf eklenebilir.', 409)

    repetition = validate_variation_count(font_data.get('repetition'))
    expected_keys = variation_key_set(repetition)
    supplied_keys = set(chars)
    invalid_keys = sorted(supplied_keys - expected_keys)
    if invalid_keys:
        raise DigitalUploadAPIError(
            'İzin verilmeyen harf anahtarı gönderildi.',
            400,
            invalid_key_sample=invalid_keys[:10],
        )

    normalized = {}
    for char_key, image_value in chars.items():
        try:
            normalized[char_key] = normalize_digital_glyph(image_value)
        except GlyphTooLargeError:
            raise
        except GlyphValidationError as exc:
            raise GlyphValidationError(f'{char_key}: {exc}') from exc

    # Validation happens before this batch, so a bad glyph never leaves a
    # partially written chunk behind.
    batch = database.batch()
    for char_key, image_base64 in normalized.items():
        char_ref = font_ref.collection('chars').document(char_key)
        batch.set(char_ref, {
            'data': image_base64,
            'updated_at': firestore.SERVER_TIMESTAMP,
        })
    batch.update(font_ref, {'updated_at': firestore.SERVER_TIMESTAMP})
    batch.commit()
    return {
        'font_id': font_id,
        'accepted_count': len(normalized),
        'status': 'draft',
    }


def _finalize_digital_upload(database, owner_id, font_id):
    font_ref, font_data = _load_digital_font(database, font_id, owner_id)
    repetition = validate_variation_count(font_data.get('repetition'))
    expected_keys = variation_key_set(repetition)
    expected_count = len(expected_keys)
    sections_completed = list(range(math.ceil(expected_count / 60)))

    if font_data.get('status') == 'ready':
        return {
            'font_id': font_id,
            'status': 'ready',
            'harf_sayisi': expected_count,
            'sections_completed': sections_completed,
            'idempotent': True,
        }
    if font_data.get('status') != 'draft':
        raise DigitalUploadAPIError('Font finalize edilebilir durumda değil.', 409)

    received_keys = {
        char_snapshot.id for char_snapshot in font_ref.collection('chars').stream()
    }
    missing_keys = sorted(expected_keys - received_keys)
    unexpected_keys = sorted(received_keys - expected_keys)
    if missing_keys or unexpected_keys:
        details = {
            'missing_count': len(missing_keys),
            'missing_sample': missing_keys[:10],
        }
        if unexpected_keys:
            details.update({
                'unexpected_count': len(unexpected_keys),
                'unexpected_sample': unexpected_keys[:10],
            })
        raise DigitalUploadAPIError(
            'Font henüz tamamlanmadı; beklenen harf seti eksik veya geçersiz.',
            409,
            **details,
        )

    transaction = database.transaction()

    @firestore.transactional
    def finish(txn):
        latest_snapshot = font_ref.get(transaction=txn)
        if not latest_snapshot.exists:
            raise DigitalUploadAPIError('Dijital font yüklemesi bulunamadı.', 404)
        latest = latest_snapshot.to_dict() or {}
        if latest.get('owner_id') != owner_id:
            raise DigitalUploadAPIError('Bu font üzerinde işlem yetkiniz yok.', 403)
        if latest.get('status') == 'ready':
            return True, None
        if latest.get('status') != 'draft':
            raise DigitalUploadAPIError('Font finalize edilebilir durumda değil.', 409)

        user_ref = database.collection('users').document(owner_id)
        user_snapshot = user_ref.get(transaction=txn)
        user_data = user_snapshot.to_dict() if user_snapshot.exists else {}
        current_credits = user_data.get('credits', 1000)
        if not isinstance(current_credits, (int, float)) or current_credits <= 0:
            raise DigitalUploadAPIError('Yetersiz kredi.', 402)
        remaining_credits = current_credits - 1

        final_fields = {
            'status': 'ready',
            'harf_sayisi': expected_count,
            'sections_completed': sections_completed,
            'source': 'digital',
            'is_public': False,
            'expected_count': expected_count,
            'credit_charged': True,
            'credit_charged_at': firestore.SERVER_TIMESTAMP,
            'finalized_at': firestore.SERVER_TIMESTAMP,
            'updated_at': firestore.SERVER_TIMESTAMP,
        }
        mirror_ref = user_ref.collection('fonts').document(font_id)
        mirror_fields = {
            'font_name': latest.get('font_name'),
            'font_id': font_id,
            'owner_id': owner_id,
            'user_id': owner_id,
            'repetition': repetition,
            'variation_count': repetition,
            'client_upload_id': latest.get('client_upload_id'),
            **final_fields,
        }
        if latest.get('created_at') is not None:
            mirror_fields['created_at'] = latest.get('created_at')

        txn.set(font_ref, final_fields, merge=True)
        txn.set(mirror_ref, mirror_fields, merge=True)
        txn.set(user_ref, {
            'credits': remaining_credits,
            'last_upload_time': firestore.SERVER_TIMESTAMP,
        }, merge=True)

        session_id = latest.get('_upload_session_id')
        if session_id:
            session_ref = database.collection('digital_font_uploads').document(session_id)
            txn.set(session_ref, {
                'status': 'ready',
                'updated_at': firestore.SERVER_TIMESTAMP,
                'finalized_at': firestore.SERVER_TIMESTAMP,
            }, merge=True)
        return False, remaining_credits

    idempotent, remaining_credits = finish(transaction)
    result = {
        'font_id': font_id,
        'status': 'ready',
        'harf_sayisi': expected_count,
        'sections_completed': sections_completed,
        'idempotent': idempotent,
    }
    if remaining_credits is not None:
        result['remaining_credits'] = remaining_credits
    return result


def _legacy_digital_upload(database, owner_id, data):
    font_name = validate_font_name(data.get('font_name'))
    repetition = validate_variation_count(data.get('variation_count', 3))
    chars = data.get('chars')
    if not isinstance(chars, dict) or not chars:
        raise DigitalUploadAPIError('chars, en az bir harf içeren nesne olmalıdır.', 400)
    if len(chars) > len(variation_key_set(repetition)):
        raise DigitalUploadAPIError('Beklenenden fazla harf gönderildi.', 400)

    client_upload_id = data.get('client_upload_id') or f'legacy-{uuid.uuid4().hex}'
    started = _start_digital_upload(
        database, owner_id, font_name, repetition, client_upload_id
    )
    font_id = started['font_id']
    items = list(chars.items())
    for offset in range(0, len(items), MAX_DRAWN_FONT_CHARS_PER_APPEND):
        _append_digital_glyphs(
            database,
            owner_id,
            font_id,
            dict(items[offset : offset + MAX_DRAWN_FONT_CHARS_PER_APPEND]),
        )
    result = _finalize_digital_upload(database, owner_id, font_id)
    result['legacy'] = True
    return result


@app.route('/api/upload_drawn_font', methods=['POST'])
@login_required
def upload_drawn_font():
    try:
        # This JSON endpoint never trusts body.user_id.  Unlike legacy form
        # routes, it requires request.uid to come from a verified Firebase token.
        if not getattr(request, 'auth_verified', False):
            return jsonify({
                'success': False,
                'message': 'Dijital font yüklemek için doğrulanmış oturum gereklidir.',
            }), 401

        if (
            request.content_length is not None
            and request.content_length > MAX_DRAWN_FONT_REQUEST_BYTES
        ):
            return jsonify({
                'success': False,
                'message': 'İstek gövdesi çok büyük (en fazla 20 MB).',
            }), 413
        raw_body = request.get_data(cache=True)
        if len(raw_body) > MAX_DRAWN_FONT_REQUEST_BYTES:
            return jsonify({
                'success': False,
                'message': 'İstek gövdesi çok büyük (en fazla 20 MB).',
            }), 413

        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise DigitalUploadAPIError('Geçerli bir JSON nesnesi gönderilmelidir.', 400)

        database = init_firebase()
        if database is None:
            return jsonify({
                'success': False,
                'message': 'Veritabanı şu anda kullanılamıyor. Lütfen tekrar deneyin.',
            }), 503

        mode = data.get('mode')
        if mode is None:
            result = _legacy_digital_upload(database, request.uid, data)
            status_code = 200
        elif mode == 'start':
            font_name = validate_font_name(data.get('font_name'))
            repetition = validate_variation_count(data.get('variation_count', 3))
            result = _start_digital_upload(
                database,
                request.uid,
                font_name,
                repetition,
                data.get('client_upload_id'),
            )
            status_code = 200 if result.get('idempotent') else 201
        elif mode == 'append':
            result = _append_digital_glyphs(
                database, request.uid, data.get('font_id'), data.get('chars')
            )
            status_code = 200
        elif mode == 'finalize':
            result = _finalize_digital_upload(
                database, request.uid, data.get('font_id')
            )
            status_code = 200
        else:
            raise DigitalUploadAPIError(
                'mode; start, append veya finalize olmalıdır.', 400
            )

        return jsonify({'success': True, **result}), status_code
    except DigitalUploadAPIError as exc:
        return jsonify({
            'success': False,
            'message': exc.message,
            **exc.details,
        }), exc.status_code
    except GlyphTooLargeError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 413
    except (GlyphValidationError, ValueError) as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400
    except Exception as e:
        logger.error(f"System error in upload_drawn_font: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'message': 'Dijital font yükleme servisi şu anda kullanılamıyor.',
        }), 503


@app.route('/api/update_char', methods=['POST'])
@login_required
def update_char():
    try:
        data = request.get_json()
        font_id, char_key, image_base64 = data.get('font_id'), data.get('char_key'), data.get('image_base64')
        user_id = request.uid # Token'dan gelen güvenli ID
        
        database = init_firebase()
        font_ref = database.collection('fonts').document(font_id)
        font_doc = font_ref.get()
        
        if not font_doc.exists: return jsonify({'success': False, 'message': 'Font bulunamadı'}), 404
        if font_doc.to_dict().get('owner_id') != user_id: return jsonify({'success': False, 'message': 'Yetkisiz işlem!'}), 403
            
        try:
            image_base64 = validate_base64_image(image_base64)
        except ValueError as e:
            return jsonify({'success': False, 'message': str(e)}), 400

        font_ref.collection('chars').document(char_key).set({'data': image_base64})
        return jsonify({'success': True, 'message': 'Harf güncellendi'})
    except ValueError as e:
        logger.warning(f"Validation error in update_char: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 400
    except Exception as e:
        logger.error(f"System error in update_char: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'message': 'Güncelleme başarısız.'}), 500

@app.route('/api/list_fonts')
def list_fonts():
    # Public okuma herkese açık, token gerekmez.
    user_id = request.args.get('user_id')
    database = init_firebase()
    if not database: return jsonify({"success": False})
    fonts = []
    try:
        public_query = database.collection('fonts').where('is_public', '==', True).stream()
        for doc in public_query:
            d = doc.to_dict()
            if d.get('status') not in (None, 'ready'):
                continue
            fonts.append({'id': doc.id, 'name': d.get('font_name'), 'char_count': d.get('harf_sayisi'), 'type': 'public', 'owner_id': d.get('owner_id')})
            
        if user_id:
            private_query = database.collection('fonts').where('owner_id', '==', user_id).where('is_public', '==', False).stream()
            for doc in private_query:
                d = doc.to_dict()
                if d.get('status') not in (None, 'ready'):
                    continue
                fonts.append({'id': doc.id, 'name': d.get('font_name'), 'char_count': d.get('harf_sayisi'), 'type': 'private', 'owner_id': user_id})
        return jsonify({"success": True, "fonts": fonts})
    except Exception as e:
        logger.error(f"System error in list_fonts: {str(e)}", exc_info=True)
        return jsonify({"success": False, "message": "Liste yüklenemedi."}), 500

@app.route('/api/add_to_library', methods=['POST'])
@login_required
def add_to_library():
    try:
        data = request.get_json()
        user_id = request.uid
        font_id = data.get('font_id')
        if not font_id: return jsonify({'success':False}), 400
        
        orig_ref = db.collection('fonts').document(font_id).get()
        if not orig_ref.exists: return jsonify({'success':False, 'message': 'Font bulunamadı'}), 404
        
        orig_data = orig_ref.to_dict()
        new_font_id = f"{user_id}_{orig_data['font_name'].replace(' ', '_')}_{str(uuid.uuid4())[:8]}"
        
        new_font_data = orig_data.copy()
        new_font_data['owner_id'] = user_id
        new_font_data['user_id'] = user_id
        new_font_data['font_id'] = new_font_id
        new_font_data['type'] = 'private'
        new_font_data['is_public'] = False
        new_font_data['created_at'] = firestore.SERVER_TIMESTAMP
        
        db.collection('fonts').document(new_font_id).set(new_font_data)
        db.collection('users').document(user_id).collection('fonts').document(new_font_id).set(new_font_data)
        
        chars = db.collection('fonts').document(font_id).collection('chars').stream()
        batch = db.batch()
        count = 0
        for char_doc in chars:
            new_char_ref = db.collection('fonts').document(new_font_id).collection('chars').document(char_doc.id)
            batch.set(new_char_ref, char_doc.to_dict())
            count += 1
            if count == 400:
                batch.commit()
                batch = db.batch()
                count = 0
        if count > 0:
            batch.commit()
            
        return jsonify({'success': True, 'new_font_id': new_font_id})
    except Exception as e:
        logger.error(f"Error in add_to_library: {e}", exc_info=True)
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/get_assets', methods=['GET'])
def get_assets():
    try:
        font_id = request.args.get('font_id')
        assets = {}
        database = init_firebase()
        if database and font_id:
            # Hibrit okuma (önce alt koleksiyon, yoksa ana doküman)
            char_docs = database.collection('fonts').document(font_id).collection('chars').stream()
            has_sub = False
            for doc in char_docs:
                has_sub = True
                key, val = doc.id, doc.to_dict().get('data')
                
                # IMPORTANT: Convert bytes (Blob) to Base64 string for JSON serialization
                if isinstance(val, bytes):
                    val = base64.b64encode(val).decode('utf-8')
                
                base_key = key.rsplit('_', 1)[0] if '_' in key else key
                if base_key not in assets: assets[base_key] = []
                assets[base_key].append(val)
                
            if not has_sub:
                doc = database.collection('fonts').document(font_id).get()
                if doc.exists:
                    raw = doc.to_dict().get('harfler', {})
                    for key, val in raw.items():
                        if isinstance(val, bytes):
                            val = base64.b64encode(val).decode('utf-8')
                        base_key = key.rsplit('_', 1)[0] if '_' in key else key
                        if base_key not in assets: assets[base_key] = []
                        assets[base_key].append(val)
            return jsonify({"success": True, "assets": assets, "source": "firebase"})
        return jsonify({"success": True, "assets": {}}), 200
    except Exception as e:
        logger.error(f"System error in get_assets: {str(e)}", exc_info=True)
        return jsonify({"success": False, "message": "Assets yüklenemedi."}), 500

@app.route('/download', methods=['POST'])
def download():
    try:
        font_id, metin = request.form.get('font_id'), request.form.get('metin', '')
        active_harfler = {}
        database = init_firebase()
        if database and font_id:
            # get_assets mantığıyla aynısını yap (Hibrit)
            char_docs = database.collection('fonts').document(font_id).collection('chars').stream()
            has_sub = False
            for doc in char_docs:
                has_sub = True
                key, b64 = doc.id, doc.to_dict().get('data')
                try:
                    img = core_generator.Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGBA")
                    parts = key.rsplit('_', 1)
                    if len(parts) == 2 and parts[1].isdigit():
                        base_key = parts[0]
                    else:
                        base_key = key
                        
                    if base_key not in active_harfler: active_harfler[base_key] = []
                    active_harfler[base_key].append(img)
                except: continue
            
            if not has_sub:
                doc = database.collection('fonts').document(font_id).get()
                if doc.exists:
                    raw = doc.to_dict().get('harfler', {})
                    for key, val in raw.items():
                        try:
                            # Eski sistemde val base64 veya url olabilir
                            if val.startswith('http'):
                                if not is_safe_url(val):
                                    logger.warning(f"Unsafe URL blocked: {val}")
                                    continue
                                try:
                                    resp = requests.get(val, timeout=5)
                                    resp.raise_for_status()
                                    img = core_generator.Image.open(io.BytesIO(resp.content)).convert("RGBA")
                                except Exception as e:
                                    logger.error(f"URL fetch error: {e}")
                                    continue
                            else:
                                b64 = val.split(",")[1] if "," in val else val
                                img = core_generator.Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGBA")
                            parts = key.rsplit('_', 1)
                            if len(parts) == 2 and parts[1].isdigit():
                                base_key = parts[0]
                            else:
                                base_key = key
                            if base_key not in active_harfler: active_harfler[base_key] = []
                            active_harfler[base_key].append(img)
                        except: continue
        
        # Renk ayari
        raw_color = request.form.get('murekkep_rengi', 'tukenmez')
        if raw_color == 'tukenmez': ink_rgb = (27, 27, 29)
        elif raw_color == 'bic_mavi': ink_rgb = (15, 82, 186)
        elif raw_color == 'kirmizi': ink_rgb = (220, 20, 60)
        elif raw_color.startswith('#'):
            raw_color = raw_color.lstrip('#')
            ink_rgb = tuple(int(raw_color[i:i+2], 16) for i in (0, 2, 4))
        else: ink_rgb = (27, 27, 29)

        config = {'page_width': 2480, 'page_height': 3508, 'margin_top': 200, 'margin_left': 150, 'margin_right': 150, 'target_letter_height': int(request.form.get('yazi_boyutu', 140)), 'line_spacing': int(request.form.get('satir_araligi', 220)), 'word_spacing': int(request.form.get('kelime_boslugu', 55)), 'murekkep_rengi': ink_rgb, 'opacity': 0.95, 'jitter': int(request.form.get('jitter', 3)), 'paper_type': request.form.get('paper_type', 'cizgili'), 'line_slope': 5}
        sayfalar = core_generator.metni_sayfaya_yaz(metin, active_harfler, config)
        
        # Overlay Ekle (Serbest Çizim)
        overlay_b64 = request.form.get('overlay_b64')
        if overlay_b64 and sayfalar:
            try:
                overlay_data = overlay_b64.split(",")[1] if "," in overlay_b64 else overlay_b64
                overlay_img = core_generator.Image.open(io.BytesIO(base64.b64decode(overlay_data))).convert("RGBA")
                # overlay_img boyutunu sayfa boyutuyla aynı yap
                if overlay_img.size != sayfalar[0].size:
                    overlay_img = overlay_img.resize(sayfalar[0].size, core_generator.Image.Resampling.LANCZOS)
                # Sadece ilk sayfaya (veya o anki ekrana) yapıştırıyoruz
                sayfalar[0].paste(overlay_img, (0, 0), overlay_img)
            except Exception as e:
                logger.error(f"Overlay error: {e}")

        pdf_buffer = core_generator.sayfalari_pdf_olustur(sayfalar)
        return send_file(pdf_buffer, mimetype='application/pdf', as_attachment=True, download_name='el_yazisi.pdf')
    except ValueError as e:
        logger.warning(f"Validation error in download: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 400
    except Exception as e:
        logger.error(f"System error in download: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'message': 'İşlem başarısız. Lütfen tekrar deneyin.'}), 500

# ─────────────────────────────────────────────────────────────────────────────
# AI ÖDEV / BELGE OLUŞTURMA ENDPOINT'LERİ
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/api/ai_generate_pdf', methods=['POST'])
@login_required
def ai_generate_pdf():
    """
    Kullanıcının fontunu kullanarak AI layout JSON veya düz metni PDF'e dönüştürür.

    Body (JSON):
      - font_id: str
      - text_content: str           – Yazdırılacak metin
      - page_settings: dict         – Global ayarlar
          (paper_type, ink_color, letter_scale, line_spacing, word_spacing,
           jitter, line_slope, margin_top, margin_left, margin_right, letter_spacing)
      - per_line_overrides: dict    – {satir_no: {param: deger}} (opsiyonel)
          Desteklenen: letter_scale, letter_spacing, word_spacing,
                       line_slope, jitter, ink_color, line_offset_y

    Returns: application/pdf binary
    """
    try:
        data = request.get_json(force=True)
        if not data:
            return jsonify({'success': False, 'message': 'JSON verisi eksik.'}), 400

        user_id            = request.uid
        font_id            = data.get('font_id', '').strip()
        page_settings      = data.get('page_settings', {})
        text_content       = data.get('text_content', '').strip()
        per_line_overrides = data.get('per_line_overrides', {})

        if not font_id:
            return jsonify({'success': False, 'message': 'font_id zorunludur.'}), 400
        if not text_content:
            return jsonify({'success': False, 'message': 'Metin içeriği boş.'}), 400
        if len(text_content) > 50000:
            return jsonify({'success': False, 'message': 'Metin çok uzun (max 50.000 karakter).'}), 400

        # Kredi kontrolü
        allowed, remaining = check_and_deduct_credit(user_id)
        if not allowed:
            return jsonify({'success': False, 'message': 'Yetersiz kredi.'}), 402

        # Font verilerini yükle
        database = init_firebase()
        font_ref = database.collection('fonts').document(font_id)
        font_doc = font_ref.get()
        if not font_doc.exists:
            return jsonify({'success': False, 'message': 'Font bulunamadı.'}), 404

        # Font karakterlerini PIL RGBA olarak yükle (core_generator v2 formatı)
        chars_stream = font_ref.collection('chars').stream()
        active_harfler = {}
        for char_doc in chars_stream:
            char_data = char_doc.to_dict()
            raw = char_data.get('data')
            if raw is None:
                continue
            try:
                raw_bytes = raw if isinstance(raw, bytes) else base64.b64decode(raw)
                pil_img   = PILImage.open(io.BytesIO(raw_bytes)).convert('RGBA')
                if char_doc.id not in active_harfler:
                    active_harfler[char_doc.id] = []
                active_harfler[char_doc.id].append(pil_img)
            except Exception as cerr:
                logger.warning(f'Karakter yüklenemedi {char_doc.id}: {cerr}')
                continue

        if not active_harfler:
            return jsonify({'success': False, 'message': 'Font karakterleri yüklenemedi.'}), 500

        # Ink rengi
        ink_color = page_settings.get('ink_color', '#1b1b1d')
        try:
            raw_c = ink_color.lstrip('#')
            ink_rgb = tuple(int(raw_c[i:i+2], 16) for i in (0, 2, 4))
        except Exception:
            ink_rgb = (27, 27, 29)

        config = {
            'page_width'          : 2480,
            'page_height'         : 3508,
            'margin_top'          : int(page_settings.get('margin_top', 220)),
            'margin_left'         : int(page_settings.get('margin_left', 180)),
            'margin_right'        : int(page_settings.get('margin_right', 180)),
            'target_letter_height': int(page_settings.get('letter_scale', 135)),
            'line_spacing'        : int(page_settings.get('line_spacing', 215)),
            'word_spacing'        : int(page_settings.get('word_spacing', 55)),
            'letter_spacing'      : int(page_settings.get('letter_spacing', 0)),
            'murekkep_rengi'      : ink_rgb,
            'opacity'             : 0.95,
            'jitter'              : int(page_settings.get('jitter', 4)),
            'paper_type'          : page_settings.get('paper_type', 'cizgili'),
            'line_slope'          : float(page_settings.get('line_slope', 3)),
        }

        # per_line_overrides: string key → int key
        plo = {int(k): v for k, v in per_line_overrides.items()} if per_line_overrides else {}

        sayfalar = core_generator.metni_sayfaya_yaz(text_content, active_harfler, config, per_line_overrides=plo)
        pdf_buffer = core_generator.sayfalari_pdf_olustur(sayfalar)

        return send_file(
            pdf_buffer,
            mimetype='application/pdf',
            as_attachment=False,
            download_name='fontify_belge.pdf'
        )

    except Exception as e:
        logger.error(f'ai_generate_pdf error: {e}', exc_info=True)
        return jsonify({'success': False, 'message': f'PDF oluşturulamadı: {str(e)}'}), 500


@app.route('/api/font_chars_meta', methods=['GET'])
@login_required
def font_chars_meta():
    """Bir fontun mevcut karakter listesini döndürür (AI prompt için özet)."""
    try:
        font_id = request.args.get('font_id', '').strip()
        if not font_id:
            return jsonify({'success': False, 'message': 'font_id zorunludur.'}), 400

        database = init_firebase()
        font_ref = database.collection('fonts').document(font_id)
        font_doc = font_ref.get()
        if not font_doc.exists:
            return jsonify({'success': False, 'message': 'Font bulunamadı.'}), 404

        font_data = font_doc.to_dict()
        chars_stream = font_ref.collection('chars').stream()
        char_list = [c.id for c in chars_stream]

        return jsonify({
            'success': True,
            'font_name': font_data.get('font_name', 'Bilinmiyor'),
            'char_count': len(char_list),
            'repetition': font_data.get('repetition', 1),
            'chars': char_list[:50]
        })
    except Exception as e:
        logger.error(f'font_chars_meta error: {e}', exc_info=True)
        return jsonify({'success': False, 'message': str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
