from flask import Flask, request, jsonify, render_template, send_file, send_from_directory, redirect
from flask_cors import CORS
import cv2
import numpy as np
import os
import base64
import firebase_admin
from firebase_admin import credentials, firestore, auth
import json
import copy
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
from typing import Any
from werkzeug.exceptions import HTTPException

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
import ai_document
import ai_provider

# Render already persists stdout/stderr. File logging is opt-in so every worker
# does not create an ever-growing app.log on ephemeral storage.
_log_handlers = [logging.StreamHandler()]
if os.environ.get("FONTIFY_LOG_FILE", "").strip():
    _log_handlers.append(logging.FileHandler(os.environ["FONTIFY_LOG_FILE"].strip()))
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=_log_handlers,
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
        hostname = (parsed.hostname or '').lower()
        if not any(hostname == domain or hostname.endswith('.' + domain) for domain in ALLOWED_IMAGE_DOMAINS):
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

DEFAULT_AI_PROVIDER_ORDER = 'gemini,groq,openai,openrouter'
DEFAULT_AI_DOCUMENT_PROVIDER_ORDER = 'groq,gemini,openai,openrouter'
DEFAULT_COPILOT_PROVIDER_ORDER = 'groq,gemini,openai,openrouter'

def _configured_frontend_origins():
    configured = os.environ.get('FRONTEND_ORIGINS', '')
    origins = [item.strip().rstrip('/') for item in configured.split(',') if item.strip()]
    if not origins:
        origins = ['https://fontify.online', 'https://www.fontify.online']
    if app.debug:
        origins.extend(['http://localhost:5500', 'http://127.0.0.1:5500'])
    return sorted(set(origins))


# Production API calls are accepted only from configured Fontify origins.
CORS(app, resources={
    r"/api/*": {"origins": _configured_frontend_origins()},
    r"/process_single": {"origins": _configured_frontend_origins()},
    r"/download": {"origins": _configured_frontend_origins()}
}, supports_credentials=False, allow_headers=[
    'Authorization', 'Content-Type', 'X-Gemini-Api-Key',
    'X-OpenRouter-Api-Key', 'X-Groq-Api-Key'
], methods=['GET', 'POST', 'PATCH', 'OPTIONS'])

@app.route('/health')
def health():
    return jsonify({"status": "healthy"}), 200


@app.route('/health/ready')
def readiness():
    """Production readiness without exposing credentials or provider keys."""
    expected_project = os.environ.get("FIREBASE_PROJECT_ID", "elyazisiapp").strip()
    firebase_ready = db is not None
    project_ready = bool(
        firebase_ready
        and connected_project_id
        and connected_project_id == expected_project
    )
    providers = ai_provider.configured_providers({
        "gemini_key": os.environ.get("GEMINI_API_KEY", ""),
        "groq_key": os.environ.get("GROQ_API_KEY", ""),
        "openai_key": os.environ.get("OPENAI_API_KEY", ""),
        "openrouter_key": os.environ.get("OPENROUTER_API_KEY", ""),
    })
    ready = firebase_ready and project_ready
    return jsonify({
        "status": "ready" if ready else "not_ready",
        "firebase_ready": firebase_ready,
        "firebase_project_ready": project_ready,
        "server_ai_provider_ready": bool(providers),
        "byok_supported": True,
    }), 200 if ready else 503

@app.route('/forms/<path:filename>')
@app.route('/pdfler/<path:filename>')
def serve_forms(filename):
    if not filename or '/' in filename or '\\' in filename or filename in {'.', '..'}:
        return jsonify({'success': False, 'message': 'Form bulunamadı.'}), 404
    # _ORNEK veya ORNEK taleplerini _DOLU olarak yönlendir (frontend uyumu için)
    if "ORNEK" in filename:
        # Eğer dosya adında 1x, 3x gibi ibareler varsa onları koru
        for v in ["1", "2", "3", "5", "10"]:
            if f"{v}x" in filename:
                return send_from_directory(
                    os.path.join(app.root_path, 'static', 'forms'),
                    f"form_{v}x_DOLU.pdf",
                )
    
    # Normal servis
    try:
        return send_from_directory(
            os.path.join(app.root_path, 'static', 'forms'), filename
        )
    except HTTPException as exc:
        if exc.code != 404:
            raise
        # Fallback: Eğer dosya bulunamazsa ama bir varyasyon isteniyorsa varsayılanı ver
        return send_from_directory(
            os.path.join(app.root_path, 'static', 'forms'), 'form_3x_BOS.pdf'
        )

# --- FIREBASE BAĞLANTISI ---
db = None
connected_project_id = "BILINMIYOR"
init_error = None

# 2. GÜVENLİK: Secret Key Env Var (Koddan Silindi)
RECAPTCHA_SECRET_KEY = os.environ.get('RECAPTCHA_SECRET_KEY')

def verify_recaptcha(token):
    required = os.environ.get('RECAPTCHA_REQUIRED', 'false').strip().lower() == 'true'
    development_bypass = app.debug or os.environ.get(
        'ALLOW_INSECURE_RECAPTCHA', ''
    ).strip().lower() == 'true'
    if development_bypass:
        logger.warning("reCAPTCHA development bypass is active.")
        return True

    if not RECAPTCHA_SECRET_KEY:
        logger.warning(
            "RECAPTCHA_SECRET_KEY is missing; required=%s. Auth and credit controls remain active.",
            required,
        )
        return not required
    
    if not token:
        logger.warning("reCAPTCHA token missing; required=%s", required)
        return not required
        
    try:
        url = "https://www.google.com/recaptcha/api/siteverify"
        data = {'secret': RECAPTCHA_SECRET_KEY, 'response': token}
        res = requests.post(url, data=data, timeout=5)
        result = res.json()
        success = bool(result.get("success", False))
        if not success:
            logger.warning(
                "reCAPTCHA rejected required=%s errors=%s hostname=%s action=%s score=%s",
                required,
                result.get('error-codes', []),
                result.get('hostname', ''),
                result.get('action', ''),
                result.get('score', ''),
            )
        return success or not required
    except Exception as e:
        logger.warning("reCAPTCHA verification unavailable; required=%s error=%s", required, type(e).__name__)
        return not required

def init_firebase():
    global db, init_error, connected_project_id
    if db is not None: return db
    try:
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
            logger.info(f"FIREBASE BAGLANTISI BASARILI | Proje: {connected_project_id}")
        else:
            logger.error("Firebase baslatilamadi: Gecerli bir anahtar yok.")
            
    except Exception as e:
        init_error = str(e)
        db = None
        logger.error(f"🔥 Firebase Hatası: {str(e)}", exc_info=True)
    return db

init_firebase()

@app.errorhandler(Exception)
def handle_exception(e):
    if isinstance(e, HTTPException):
        return jsonify({"success": False, "message": e.description}), e.code
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
        raw_host = request.host.strip().lower()
        host_name = (
            raw_host[1:].partition(']')[0]
            if raw_host.startswith('[')
            else raw_host.partition(':')[0]
        )
        remote_address = str(request.remote_addr or '').strip().lower()
        is_loopback = (
            host_name in {'localhost', '127.0.0.1', '::1'}
            and remote_address in {'127.0.0.1', '::1'}
        )
        if not app.debug and not is_loopback:
            from flask import redirect
            return redirect(request.url.replace('http://', 'https://'), code=301)

@app.after_request
def set_secure_headers(response):
    """Güvenlik header'ları ekle"""
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
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

def verified_login_required(f):
    """Require a valid Firebase Bearer token; never trust a client supplied UID."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if request.method == 'OPTIONS':
            return '', 204
            
        authorization = request.headers.get('Authorization', '').strip()
        parts = authorization.split()
        if len(parts) != 2 or parts[0].lower() != 'bearer' or not parts[1]:
            return jsonify({'success': False, 'message': 'Güvenli oturum gerekli.'}), 401
        try:
            decoded = auth.verify_id_token(parts[1])
            request.uid = decoded['uid']
            request.auth_verified = True
        except Exception as exc:
            logger.warning('Firebase token doğrulanamadı: %s', type(exc).__name__)
            return jsonify({'success': False, 'message': 'Oturum doğrulanamadı. Lütfen yeniden giriş yapın.'}), 401
        return f(*args, **kwargs)
    return decorated_function


def optional_verified_uid():
    """Return the verified Firebase UID, or None when no token was supplied."""
    authorization = request.headers.get('Authorization', '').strip()
    if not authorization:
        return None
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != 'bearer' or not parts[1]:
        raise ValueError('Geçersiz Authorization başlığı.')
    try:
        return auth.verify_id_token(parts[1])['uid']
    except Exception as exc:
        logger.warning('Opsiyonel Firebase token doğrulanamadı: %s', type(exc).__name__)
        raise ValueError('Oturum doğrulanamadı.') from exc

# --- KREDİ SİSTEMİ ---
def check_and_deduct_credit(user_id):
    """Atomically deduct one credit; billing failures never grant free work."""
    try:
        if not db or not user_id:
            return False, 0
        default_credits = max(0, int(os.environ.get('DEFAULT_USER_CREDITS', '10')))
        user_ref = db.collection('users').document(user_id)
        transaction = db.transaction()

        @firestore.transactional
        def deduct(txn):
            snapshot = user_ref.get(transaction=txn)
            data = snapshot.to_dict() if snapshot.exists else {}
            current = int(data.get('credits', default_credits))
            if current <= 0:
                return False, 0
            remaining = current - 1
            txn.set(user_ref, {'credits': remaining}, merge=True)
            return True, remaining

        return deduct(transaction)
    except Exception as e:
        logger.error(f"Credit error: {e}")
        return False, 0


def charge_mobile_session_once(session_ref, user_id):
    """Charge a mobile upload session exactly once, even under concurrent requests."""
    if not db or not session_ref or not user_id:
        return False, 0
    try:
        default_credits = max(0, int(os.environ.get('DEFAULT_USER_CREDITS', '10')))
        user_ref = db.collection('users').document(user_id)
        transaction = db.transaction()

        @firestore.transactional
        def charge(txn):
            session_snapshot = session_ref.get(transaction=txn)
            if not session_snapshot.exists:
                return False, 0
            session = session_snapshot.to_dict() or {}
            if session.get('owner_id') != user_id or int(session.get('expires_at', 0)) < int(time.time()):
                return False, 0
            if session.get('credit_charged'):
                return True, None
            user_snapshot = user_ref.get(transaction=txn)
            user_data = user_snapshot.to_dict() if user_snapshot.exists else {}
            current = int(user_data.get('credits', default_credits))
            if current <= 0:
                return False, 0
            remaining = current - 1
            txn.set(user_ref, {'credits': remaining}, merge=True)
            txn.update(session_ref, {
                'credit_charged': True,
                'first_upload_at': firestore.SERVER_TIMESTAMP,
            })
            return True, remaining

        return charge(transaction)
    except Exception as exc:
        logger.error('Mobile session credit error: %s', type(exc).__name__, exc_info=True)
        return False, 0

@app.route('/api/get_user_credits')
@verified_login_required
def get_user_credits():
    user_id = request.uid
    
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
                'harf_sayisi': 0, 'sections_completed': [], 'is_public': False
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


def _configured_max_upload_size() -> int:
    """Return a bounded upload limit suitable for multi-page 10x forms."""
    try:
        megabytes = int(os.environ.get('MAX_FORM_UPLOAD_SIZE_MB', '25'))
    except (TypeError, ValueError):
        megabytes = 25
    # A 10x form has nine A4 pages. 25 MB accepts normal iPad/scanner PDFs
    # while the 30 MB ceiling keeps the free Render worker within a safe range.
    return max(5, min(megabytes, 30)) * 1024 * 1024


MAX_FILE_SIZE = _configured_max_upload_size()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/api/upload_form', methods=['POST'])
@verified_login_required
def upload_form():
    try:
        user_id = request.uid
        if request.content_length and request.content_length > MAX_FILE_SIZE + (512 * 1024):
            return jsonify({'success': False, 'message': 'Dosya en fazla 10 MB olabilir.'}), 413
        
        # 1. Bot koruması
        if not verify_recaptcha(request.form.get('recaptcha_token')):
            logger.warning(f"reCAPTCHA validation failed - User: {user_id}")
            return jsonify({'success': False, 'message': 'Güvenlik doğrulaması başarısız.'}), 403

        # 2. Dosya Kontrolü
        uploaded_files = request.files.getlist('file') or request.files.getlist('files')
        
        if not uploaded_files or not uploaded_files[0].filename:
            return jsonify({'success': False, 'message': 'Dosya yüklenmedi.'}), 400
            
        file = uploaded_files[0]
        
        if not allowed_file(file.filename):
            return jsonify({'success': False, 'message': 'Geçersiz dosya türü.'}), 400

        try:
            font_name = validate_font_name(request.form.get('font_name'))
        except ValueError as e:
            return jsonify({'success': False, 'message': str(e)}), 400

        variation_count = validate_variation_count(request.form.get('variation_count', 3))
        file_bytes = file.stream.read(MAX_FILE_SIZE + 1)
        if len(file_bytes) > MAX_FILE_SIZE:
            return jsonify({'success': False, 'message': 'Dosya en fazla 10 MB olabilir.'}), 413
        extension = file.filename.rsplit('.', 1)[1].lower()
        if extension == 'pdf':
            if not file_bytes.startswith(b'%PDF-'):
                return jsonify({'success': False, 'message': 'Dosya geçerli bir PDF değil.'}), 400
        else:
            try:
                with PILImage.open(io.BytesIO(file_bytes)) as probe:
                    probe.verify()
                    if (probe.format or '').upper() not in {'PNG', 'JPEG', 'JPG'}:
                        raise ValueError('unsupported image')
            except Exception:
                return jsonify({'success': False, 'message': 'Dosya geçerli bir PNG/JPEG değil.'}), 400

        database = init_firebase()
        if database is None:
            return jsonify({'success': False, 'message': 'Veritabanı şu anda kullanılamıyor.'}), 503

        # Kredi ancak tüm doğrulamalar başarıyla geçtikten sonra düşülür.
        allowed, msg = check_and_deduct_credit(user_id)
        if not allowed:
            return jsonify({'success': False, 'message': msg}), 402

        try:
            database.collection('users').document(user_id).update({'last_upload_time': firestore.SERVER_TIMESTAMP})
        except Exception:
            pass
        
        job_id = str(uuid.uuid4())
        if database:
            database.collection('operations').document(job_id).set({
                'status': 'queued', 'progress': 0, 'user_id': user_id, 
                'created_at': firestore.SERVER_TIMESTAMP, 'type': 'pdf_upload'
            })
        threading.Thread(target=process_pdf_job, args=(job_id, user_id, font_name, variation_count, file_bytes), daemon=True).start()
        return jsonify({'success': True, 'job_id': job_id})
    except ValueError as e:
        logger.warning(f"Validation error in upload_form: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 400
    except Exception as e:
        logger.error(f"System error in upload_form: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'message': 'İşlem başarısız. Lütfen tekrar deneyin.'}), 500


@app.route('/api/operations/<job_id>', methods=['GET', 'OPTIONS'])
@verified_login_required
def get_operation_status(job_id):
    """Return one user's upload job state for the Firestore-offline fallback."""
    try:
        job_id = str(uuid.UUID(str(job_id)))
    except (TypeError, ValueError, AttributeError):
        return jsonify({'success': False, 'message': 'İşlem kimliği geçersiz.'}), 400

    database = init_firebase()
    if database is None:
        return jsonify({'success': False, 'message': 'İşlem durumu şu anda kullanılamıyor.'}), 503

    try:
        snapshot = database.collection('operations').document(job_id).get()
        if not snapshot.exists:
            return jsonify({'success': False, 'message': 'İşlem bulunamadı.'}), 404
        operation = snapshot.to_dict() or {}
    except Exception as exc:
        logger.warning('Operation status lookup failed: %s', type(exc).__name__)
        return jsonify({'success': False, 'message': 'İşlem durumu şu anda kullanılamıyor.'}), 503

    # Respond as not-found rather than disclosing another user's operation ID.
    if not isinstance(operation, dict) or operation.get('user_id') != request.uid:
        return jsonify({'success': False, 'message': 'İşlem bulunamadı.'}), 404

    status = str(operation.get('status') or 'queued').lower()
    if status not in {'queued', 'processing', 'completed', 'error'}:
        status = 'processing'
    try:
        progress = max(0, min(100, int(operation.get('progress') or 0)))
    except (TypeError, ValueError):
        progress = 0
    try:
        processed_chars = max(0, int(operation.get('processed_chars') or 0))
    except (TypeError, ValueError):
        processed_chars = 0

    # Only expose the fields the progress UI needs; internal worker exceptions
    # remain in server logs.
    public_operation = {
        'status': status,
        'progress': progress,
        'message': '' if status == 'error' else str(operation.get('message') or '')[:240],
        'processed_chars': processed_chars,
        'font_id': str(operation.get('font_id') or '')[:256],
    }
    return jsonify({'success': True, 'operation': public_operation})

@app.route('/api/mobile_upload_session', methods=['POST'])
@verified_login_required
def create_mobile_upload_session():
    try:
        data = request.get_json(silent=True) or {}
        font_name = validate_font_name(data.get('font_name'))
        repetition = validate_variation_count(data.get('variation_count', 3))
        database = init_firebase()
        if database is None:
            return jsonify({'success': False, 'message': 'Veritabanı kullanılamıyor.'}), 503
        session_id = uuid.uuid4().hex
        expires_at = int(time.time()) + 3600
        database.collection('mobile_upload_sessions').document(session_id).set({
            'owner_id': request.uid,
            'font_name': font_name,
            'variation_count': repetition,
            'expires_at': expires_at,
            'credit_charged': False,
            'created_at': firestore.SERVER_TIMESTAMP,
        })
        return jsonify({'success': True, 'session_id': session_id, 'expires_at': expires_at})
    except ValueError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400
    except Exception as exc:
        logger.error('Mobile session error: %s', type(exc).__name__, exc_info=True)
        return jsonify({'success': False, 'message': 'Mobil yükleme oturumu oluşturulamadı.'}), 500


@app.route('/process_single', methods=['POST'])
def process_single():
    try:
        data = request.get_json(silent=True) or {}
        if not verify_recaptcha(data.get('recaptcha_token')): return jsonify({'success': False, 'message': 'Güvenlik doğrulaması başarısız.'}), 403

        try:
            database = init_firebase()
            if database is None:
                return jsonify({'success': False, 'message': 'Veritabanı kullanılamıyor.'}), 503
            session_id = str(data.get('session_id', '')).strip()
            session_ref = None
            session_data = None
            if re.fullmatch(r'[0-9a-f]{32}', session_id):
                session_ref = database.collection('mobile_upload_sessions').document(session_id)
                session_snapshot = session_ref.get()
                if not session_snapshot.exists:
                    return jsonify({'success': False, 'message': 'Mobil yükleme oturumu bulunamadı.'}), 401
                session_data = session_snapshot.to_dict() or {}
                if int(session_data.get('expires_at', 0)) < int(time.time()):
                    return jsonify({'success': False, 'message': 'Mobil yükleme oturumunun süresi doldu.'}), 401
                u_id = session_data.get('owner_id')
                f_name = validate_font_name(session_data.get('font_name'))
                repetition = validate_variation_count(session_data.get('variation_count'))
            elif app.debug or os.environ.get('ALLOW_LEGACY_MOBILE_UPLOADS', '').lower() == 'true':
                u_id = data.get('user_id')
                f_name = validate_font_name(data.get('font_name'))
                repetition = validate_variation_count(data.get('variation_count', 3))
            else:
                return jsonify({'success': False, 'message': 'Güvenli mobil yükleme oturumu gerekli.'}), 401
            b64 = validate_base64_image(data.get('image_base64'))
        except ValueError as e:
            return jsonify({'success': False, 'message': str(e)}), 400

        if session_ref is not None:
            allowed, msg = charge_mobile_session_once(session_ref, u_id)
            if not allowed:
                return jsonify({'success': False, 'message': 'Yetersiz kredi veya geçersiz mobil oturum.'}), 402
        elif session_ref is None:
            allowed, msg = check_and_deduct_credit(u_id)
            if not allowed:
                return jsonify({'success': False, 'message': 'Yetersiz kredi.'}), 402

        h_sistemi = HarfSistemi(repetition=repetition)
        nparr = np.frombuffer(base64.b64decode(b64), np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None: return jsonify({'success': False, 'message': 'Resim hatası'}), 400

        res, err = h_sistemi.process_single_page(img)
        if err: return jsonify({'success': False, 'message': err}), 400
        
        if database:
            fid = f"{u_id}_{f_name.replace(' ', '_')}"
            d_ref = database.collection('fonts').document(fid)
            u_ref = database.collection('users').document(u_id).collection('fonts').document(fid)
            
            if not d_ref.get().exists:
                payload = {'font_name': f_name, 'font_id': fid, 'owner_id': u_id, 'user_id': u_id, 'repetition': repetition, 'created_at': firestore.SERVER_TIMESTAMP, 'harf_sayisi': 0, 'sections_completed': [], 'is_public': False}
                d_ref.set(payload); u_ref.set(payload)
            
            batch = database.batch()
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
@verified_login_required
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
            
        new_status = not doc.to_dict().get('is_public', False)
        font_ref.update({'is_public': new_status})
        return jsonify({'success': True, 'new_status': new_status})
    except ValueError as e:
        logger.warning(f"Validation error in toggle_visibility: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 400
    except Exception as e:
        logger.error(f"System error in toggle_visibility: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'message': 'İşlem başarısız.'}), 500


@app.route('/api/delete_font', methods=['POST'])
@verified_login_required
def delete_font():
    """Remove a library entry, or delete the full font when the requester owns it."""
    try:
        data = request.get_json(silent=True) or {}
        font_id = str(data.get('font_id', '')).strip()
        if len(font_id) < 3 or len(font_id) > 180 or '/' in font_id or '..' in font_id or re.search(r'[\x00-\x1f]', font_id):
            return jsonify({'success': False, 'message': 'Geçersiz font kimliği.'}), 400
        database = init_firebase()
        if database is None:
            return jsonify({'success': False, 'message': 'Veritabanı kullanılamıyor.'}), 503
        user_font_ref = database.collection('users').document(request.uid).collection('fonts').document(font_id)
        font_ref = database.collection('fonts').document(font_id)
        snapshot = font_ref.get()
        if not snapshot.exists or (snapshot.to_dict() or {}).get('owner_id') != request.uid:
            user_font_ref.delete()
            return jsonify({'success': True, 'deleted': 'library_entry'})

        pending = []
        for char_doc in font_ref.collection('chars').stream():
            pending.append(char_doc.reference)
            if len(pending) == 400:
                batch = database.batch()
                for reference in pending:
                    batch.delete(reference)
                batch.commit()
                pending = []
        if pending:
            batch = database.batch()
            for reference in pending:
                batch.delete(reference)
            batch.commit()
        batch = database.batch()
        batch.delete(font_ref)
        batch.delete(user_font_ref)
        batch.commit()
        return jsonify({'success': True, 'deleted': 'font'})
    except Exception as exc:
        logger.error('Delete font error: %s', type(exc).__name__, exc_info=True)
        return jsonify({'success': False, 'message': 'Font silinemedi.'}), 500

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
        try:
            default_credits = max(0, int(os.environ.get('DEFAULT_USER_CREDITS', '10')))
        except (TypeError, ValueError):
            default_credits = 10
        current_credits = user_data.get('credits', default_credits)
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
@verified_login_required
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
@verified_login_required
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
    database = init_firebase()
    if not database:
        return jsonify({"success": False, "message": "Veritabanı kullanılamıyor."}), 503
    fonts = []
    try:
        user_id = optional_verified_uid()
        seen = set()
        public_query = database.collection('fonts').where('is_public', '==', True).stream()
        for doc in public_query:
            d = doc.to_dict()
            if d.get('status') not in (None, 'ready'):
                continue
            seen.add(doc.id)
            fonts.append({'id': doc.id, 'name': d.get('font_name'), 'char_count': d.get('harf_sayisi'), 'type': 'public', 'owner_id': d.get('owner_id')})

        if user_id:
            owned_query = database.collection('users').document(user_id).collection('fonts').stream()
            for doc in owned_query:
                if doc.id in seen:
                    continue
                d = doc.to_dict()
                if d.get('status') not in (None, 'ready'):
                    continue
                fonts.append({'id': doc.id, 'name': d.get('font_name'), 'char_count': d.get('harf_sayisi'), 'type': 'private' if not d.get('is_public', False) else 'owned', 'owner_id': user_id})
        return jsonify({"success": True, "fonts": fonts})
    except ValueError as e:
        return jsonify({"success": False, "message": str(e)}), 401
    except Exception as e:
        logger.error(f"System error in list_fonts: {str(e)}", exc_info=True)
        return jsonify({"success": False, "message": "Liste yüklenemedi."}), 500

@app.route('/api/add_to_library', methods=['POST'])
@verified_login_required
def add_to_library():
    try:
        data = request.get_json()
        user_id = request.uid
        font_id = data.get('font_id')
        if not font_id: return jsonify({'success':False}), 400
        
        orig_ref = db.collection('fonts').document(font_id).get()
        if not orig_ref.exists: return jsonify({'success':False, 'message': 'Font bulunamadı'}), 404
        
        orig_data = orig_ref.to_dict()
        if orig_data.get('status') not in (None, 'ready'):
            return jsonify({'success': False, 'message': 'Taslak font kütüphaneye eklenemez.'}), 409
        if not orig_data.get('is_public', False) and orig_data.get('owner_id') != user_id:
            return jsonify({'success': False, 'message': 'Bu özel fontu kopyalama yetkiniz yok.'}), 403
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
            font_ref = database.collection('fonts').document(font_id)
            font_snapshot = font_ref.get()
            font_data = font_snapshot.to_dict() if font_snapshot.exists else {}
            if font_snapshot.exists:
                if font_data.get('source') == 'digital' and font_data.get('status') != 'ready':
                    return jsonify({'success': False, 'message': 'Font henüz tamamlanmadı.'}), 409
                if not font_data.get('is_public', False):
                    try:
                        requester = optional_verified_uid()
                    except ValueError as exc:
                        return jsonify({'success': False, 'message': str(exc)}), 401
                    if requester != font_data.get('owner_id'):
                        return jsonify({'success': False, 'message': 'Bu özel fonta erişim yetkiniz yok.'}), 403
            # Hibrit okuma (önce alt koleksiyon, yoksa ana doküman)
            char_docs = list(font_ref.collection('chars').stream())
            char_docs.sort(key=lambda item: (
                re.sub(r'_\d+$', '', item.id),
                int(item.id.rsplit('_', 1)[1]) if item.id.rsplit('_', 1)[-1].isdigit() else 0
            ))
            has_sub = False
            for doc in char_docs:
                has_sub = True
                key, val = doc.id, doc.to_dict().get('data')
                
                # IMPORTANT: Convert bytes (Blob) to Base64 string for JSON serialization
                if not isinstance(val, str):
                    try:
                        val = base64.b64encode(bytes(val)).decode('utf-8')
                    except Exception:
                        continue
                
                base_key = key.rsplit('_', 1)[0] if '_' in key else key
                if base_key not in assets: assets[base_key] = []
                assets[base_key].append(val)
                
            if not has_sub:
                if font_snapshot.exists:
                    raw = font_data.get('harfler', {})
                    for key, val in raw.items():
                        if isinstance(val, bytes):
                            val = base64.b64encode(val).decode('utf-8')
                        base_key = key.rsplit('_', 1)[0] if '_' in key else key
                        if base_key not in assets: assets[base_key] = []
                        assets[base_key].append(val)
            return jsonify({"success": True, "assets": assets, "source": "firebase", "font": {
                "id": font_id,
                "name": font_data.get('font_name') if font_data else None,
                "repetition": font_data.get('repetition', 1) if font_data else 1,
            }})
        return jsonify({"success": True, "assets": {}}), 200
    except Exception as e:
        logger.error(f"System error in get_assets: {str(e)}", exc_info=True)
        return jsonify({"success": False, "message": "Assets yüklenemedi."}), 500

@app.route('/download', methods=['POST'])
def download():
    try:
        font_id, metin = request.form.get('font_id'), request.form.get('metin', '')
        if request.content_length and request.content_length > 12 * 1024 * 1024:
            return jsonify({'success': False, 'message': 'İstek gövdesi çok büyük.'}), 413
        if not isinstance(metin, str) or len(metin) > ai_document.MAX_DOCUMENT_CHARS:
            return jsonify({
                'success': False,
                'message': f'Metin en fazla {ai_document.MAX_DOCUMENT_CHARS:,} karakter olabilir.',
            }), 413
        active_harfler = {}
        database = init_firebase()
        if database and font_id:
            font_ref = database.collection('fonts').document(font_id)
            font_snapshot = font_ref.get()
            if not font_snapshot.exists:
                return jsonify({'success': False, 'message': 'Font bulunamadı.'}), 404
            font_data = font_snapshot.to_dict() or {}
            if not font_data.get('is_public', False):
                id_token = request.form.get('id_token', '')
                try:
                    requester = auth.verify_id_token(id_token).get('uid')
                except Exception:
                    return jsonify({'success': False, 'message': 'Özel font için güvenli oturum gerekli.'}), 401
                if requester != font_data.get('owner_id'):
                    return jsonify({'success': False, 'message': 'Bu özel fonta erişim yetkiniz yok.'}), 403
            # Reuse the same bounded, SSRF-safe and decompression-safe loader as
            # the AI/PDF endpoints instead of maintaining an unsafe legacy copy.
            active_harfler = _load_font_images(font_ref)
        
        # Renk ayari
        raw_color = request.form.get('murekkep_rengi', 'tukenmez')
        if raw_color == 'tukenmez': ink_rgb = (27, 27, 29)
        elif raw_color == 'bic_mavi': ink_rgb = (15, 82, 186)
        elif raw_color == 'kirmizi': ink_rgb = (220, 20, 60)
        elif re.fullmatch(r'#[0-9a-fA-F]{6}', raw_color):
            hex_color = raw_color.lstrip('#')
            ink_rgb = tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))
        else: ink_rgb = (27, 27, 29)

        def bounded_form_int(name, default, minimum, maximum):
            try:
                value = int(request.form.get(name, default))
            except (TypeError, ValueError):
                value = default
            return max(minimum, min(maximum, value))

        paper_type = request.form.get('paper_type', 'cizgili')
        if paper_type not in {'cizgili', 'kareli', 'duz'}:
            paper_type = 'cizgili'
        config = {
            'page_width': 2480, 'page_height': 3508,
            'margin_top': 200, 'margin_left': 150, 'margin_right': 150,
            'target_letter_height': bounded_form_int('yazi_boyutu', 140, 35, 500),
            'line_spacing': bounded_form_int('satir_araligi', 220, 45, 650),
            'word_spacing': bounded_form_int('kelime_boslugu', 55, 0, 300),
            'murekkep_rengi': ink_rgb, 'opacity': 0.95,
            'jitter': bounded_form_int('jitter', 3, 0, 30),
            'paper_type': paper_type, 'line_slope': 5,
        }
        sayfalar = list(core_generator.metni_sayfaya_yaz(metin, active_harfler, config))
        
        # Overlay Ekle (Serbest Çizim)
        overlay_b64 = request.form.get('overlay_b64')
        if overlay_b64 and sayfalar:
            try:
                overlay_data = overlay_b64.split(",")[1] if "," in overlay_b64 else overlay_b64
                if len(overlay_data) > 10_000_000:
                    raise ValueError('Çizim katmanı çok büyük.')
                overlay_bytes = base64.b64decode(overlay_data, validate=True)
                if len(overlay_bytes) > 8 * 1024 * 1024:
                    raise ValueError('Çizim katmanı çok büyük.')
                with core_generator.Image.open(io.BytesIO(overlay_bytes)) as source_overlay:
                    if (
                        source_overlay.width > 5000
                        or source_overlay.height > 5000
                        or source_overlay.width * source_overlay.height > 20_000_000
                    ):
                        raise ValueError('Çizim katmanı boyut sınırını aşıyor.')
                    source_overlay.load()
                    overlay_img = source_overlay.convert("RGBA")
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
# AI DOCUMENT STUDIO
# ─────────────────────────────────────────────────────────────────────────────

def _font_access_for_user(font_id, user_id, *, require_ready=True):
    if not isinstance(font_id, str) or not font_id.strip() or len(font_id) > 180:
        raise ai_document.AiDocumentError('Geçerli bir font_id gerekli.')
    database = init_firebase()
    if database is None:
        raise ai_document.AiDocumentError('Veritabanı şu anda kullanılamıyor.', 503)
    font_ref = database.collection('fonts').document(font_id.strip())
    snapshot = font_ref.get()
    if not snapshot.exists:
        raise ai_document.AiDocumentError('Font bulunamadı.', 404)
    font_data = snapshot.to_dict() or {}
    is_public = bool(font_data.get('is_public', False))
    if not is_public and font_data.get('owner_id') != user_id:
        raise ai_document.AiDocumentError('Bu font için erişim yetkiniz yok.', 403)
    if require_ready and font_data.get('source') == 'digital' and font_data.get('status') != 'ready':
        raise ai_document.AiDocumentError('Bu font henüz tamamlanmamış bir taslak.', 409)
    return font_ref, font_data


def _raw_character_bytes(raw):
    if isinstance(raw, str):
        encoded = raw.split(',', 1)[1] if ',' in raw else raw
        return base64.b64decode(encoded, validate=True)
    if isinstance(raw, (bytes, bytearray, memoryview)):
        return bytes(raw)
    try:
        return bytes(raw)
    except Exception as exc:
        raise ValueError('Karakter verisi okunamadı.') from exc


def _gemini_api_key():
    """Prefer a per-request BYOK key, then the Render-only server secret."""
    return ai_document.choose_api_key(
        request.headers.get('X-Gemini-Api-Key'),
        os.environ.get('GEMINI_API_KEY'),
    )


def _request_ui_language(data=None):
    """Use explicit JSON preference first, then the standard HTTP language."""
    explicit = data.get('ui_language') if isinstance(data, dict) else None
    if str(explicit or '').strip().lower().startswith('en'):
        return 'en'
    if explicit is not None:
        return 'tr'
    accepted = str(request.headers.get('Accept-Language') or '').lower()
    return 'en' if accepted.startswith('en') else 'tr'


def _ai_provider_config(provider_order=None):
    """Collect optional BYOK/server credentials without exposing them."""
    return {
        "gemini_key": (
            request.headers.get("X-Gemini-Api-Key")
            or os.environ.get("GEMINI_API_KEY", "")
        ).strip(),
        "gemini_model": os.environ.get("GEMINI_MODEL", ai_document.DEFAULT_GEMINI_MODEL),
        "groq_key": (
            request.headers.get("X-Groq-Api-Key")
            or os.environ.get("GROQ_API_KEY", "")
        ).strip(),
        "groq_model_timeout_ms": os.environ.get("GROQ_MODEL_TIMEOUT_MS", "30000").strip(),
        "openai_key": os.environ.get("OPENAI_API_KEY", "").strip(),
        "openai_model": os.environ.get(
            "OPENAI_MODEL", ai_provider.DEFAULT_OPENAI_MODEL
        ).strip(),
        "openrouter_key": (
            request.headers.get("X-OpenRouter-Api-Key")
            or os.environ.get("OPENROUTER_API_KEY", "")
        ).strip(),
        "openrouter_model": os.environ.get(
            "OPENROUTER_MODEL", ai_provider.DEFAULT_OPENROUTER_MODEL
        ).strip(),
        "provider_order": str(
            provider_order
            or os.environ.get("AI_PROVIDER_ORDER", DEFAULT_AI_PROVIDER_ORDER)
        ).strip(),
    }


def _load_glyph_value(raw):
    if isinstance(raw, str) and raw.lower().startswith('https://'):
        if not is_safe_url(raw):
            raise ValueError('Güvenli olmayan karakter URL adresi.')
        response = requests.get(raw, timeout=8, stream=True)
        response.raise_for_status()
        content_length = int(response.headers.get('Content-Length', '0') or 0)
        if content_length > 2 * 1024 * 1024:
            raise ValueError('Karakter görseli çok büyük.')
        chunks = []
        downloaded = 0
        for chunk in response.iter_content(64 * 1024):
            downloaded += len(chunk)
            if downloaded > 2 * 1024 * 1024:
                raise ValueError('Karakter görseli çok büyük.')
            chunks.append(chunk)
        raw_bytes = b''.join(chunks)
    else:
        raw_bytes = _raw_character_bytes(raw)
    if len(raw_bytes) > 2 * 1024 * 1024:
        raise ValueError('Karakter görseli çok büyük.')
    with PILImage.open(io.BytesIO(raw_bytes)) as image:
        if (
            image.width > 2048
            or image.height > 2048
            or image.width * image.height > 4_194_304
        ):
            raise ValueError('Karakter görseli boyut sınırını aşıyor.')
        image.load()
        return image.convert('RGBA')


def _append_font_glyph(grouped, storage_key, raw):
    glyph = _load_glyph_value(raw)
    match = re.match(r'^(.*)_(\d+)$', storage_key)
    base_key = match.group(1) if match else storage_key
    grouped.setdefault(base_key, []).append(glyph)


def _load_font_images(font_ref):
    """Load both current subcollection fonts and legacy main-document fonts."""
    grouped = {}
    documents = list(font_ref.collection('chars').stream())
    if len(documents) > 2000:
        raise ai_document.AiDocumentError('Font karakter sınırını aşıyor.', 413)
    documents.sort(key=lambda item: item.id)
    for char_doc in documents:
        raw = (char_doc.to_dict() or {}).get('data')
        if raw is None:
            continue
        try:
            _append_font_glyph(grouped, char_doc.id, raw)
        except Exception as exc:
            logger.warning('Font karakteri atlandı (%s): %s', char_doc.id, type(exc).__name__)

    # Paper-scanned legacy fonts (including the existing 3x font) may keep all
    # glyphs in the parent document's `harfler` map instead of /chars.
    if not grouped:
        snapshot = font_ref.get()
        legacy_map = (snapshot.to_dict() or {}).get('harfler', {}) if snapshot.exists else {}
        if isinstance(legacy_map, dict) and len(legacy_map) <= 2000:
            grouped.update(ai_document.decode_embedded_font_map(legacy_map))
            for storage_key, raw in sorted(legacy_map.items()):
                values = raw if isinstance(raw, list) else [raw]
                for value in values[:10]:
                    if not (isinstance(value, str) and value.lower().startswith('https://')):
                        continue
                    try:
                        _append_font_glyph(grouped, str(storage_key), value)
                    except Exception as exc:
                        logger.warning('Eski font karakteri atlandı (%s): %s', storage_key, type(exc).__name__)
    if not grouped:
        raise ai_document.AiDocumentError('Font karakterleri yüklenemedi.', 422)
    return grouped


def _load_secondary_font(data, user_id, primary_font_id):
    secondary_id = str((data or {}).get('secondary_font_id') or '').strip()
    if not secondary_id:
        return None, None
    if secondary_id == str(primary_font_id or '').strip():
        raise ai_document.AiDocumentError('Çoklu yazar için ikinci ve farklı bir font seçmelisin.')
    secondary_ref, secondary_data = _font_access_for_user(secondary_id, user_id)
    return _load_font_images(secondary_ref), secondary_data


def _ai_error_response(exc):
    if isinstance(exc, ai_document.AiDocumentError):
        return jsonify({'success': False, 'message': str(exc)}), exc.status_code
    if isinstance(exc, ai_provider.AiProviderError):
        return jsonify({'success': False, 'message': str(exc)}), exc.status_code
    logger.error('AI Document Studio error: %s', type(exc).__name__, exc_info=True)
    return jsonify({'success': False, 'message': 'Belge işlemi şu anda tamamlanamadı.'}), 500


@app.route('/api/ai/status', methods=['GET'])
@verified_login_required
def ai_status():
    copilot_model = os.environ.get('COPILOT_GEMINI_MODEL', ai_document.DEFAULT_GEMINI_MODEL)
    try:
        copilot_model = ai_document.validate_model(copilot_model)
    except ValueError:
        copilot_model = ai_document.DEFAULT_GEMINI_MODEL
    server_config = {
        "gemini_key": os.environ.get("GEMINI_API_KEY", ""),
        "groq_key": os.environ.get("GROQ_API_KEY", ""),
        "openai_key": os.environ.get("OPENAI_API_KEY", ""),
        "openrouter_key": os.environ.get("OPENROUTER_API_KEY", ""),
    }
    providers = ai_provider.configured_providers(server_config)
    return jsonify({
        'success': True,
        'server_key_configured': bool(providers),
        'configured_providers': providers,
        'provider_order': os.environ.get('AI_PROVIDER_ORDER', DEFAULT_AI_PROVIDER_ORDER),
        'document_provider_order': os.environ.get(
            'AI_DOCUMENT_PROVIDER_ORDER', DEFAULT_AI_DOCUMENT_PROVIDER_ORDER
        ),
        'copilot_provider_order': os.environ.get(
            'COPILOT_PROVIDER_ORDER', DEFAULT_COPILOT_PROVIDER_ORDER
        ),
        'default_model': ai_document.DEFAULT_GEMINI_MODEL,
        'allowed_models': list(ai_document.allowed_models()),
        'copilot_model': copilot_model,
    })


@app.route('/api/ai/test', methods=['POST', 'OPTIONS'])
@verified_login_required
def ai_connection_test():
    try:
        data = request.get_json(silent=True) or {}
        model = data.get('model', ai_document.DEFAULT_GEMINI_MODEL)
        config = _ai_provider_config()
        config['gemini_model'] = ai_document.validate_model(model)
        
        m_lower = model.lower()
        if "llama" in m_lower or "mixtral" in m_lower or "gemma" in m_lower:
            config["provider_order"] = "groq"
            if "gemini_key" in config: del config["gemini_key"]
        elif "gemini" in m_lower:
            config["provider_order"] = "gemini"
            
        provider, actual_model = ai_provider.test_provider_chain(
            config=config,
            gemini_test=lambda key: ai_document.test_gemini_connection(key, model),
        )
        return jsonify({'success': True, 'provider': provider, 'model': actual_model})
    except Exception as exc:
        return _ai_error_response(exc)


@app.route('/api/ai/plan', methods=['POST', 'OPTIONS'])
@verified_login_required
def ai_document_plan():
    try:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ai_document.AiDocumentError('Geçerli bir JSON gövdesi gerekli.')
        font_ref, font_data = _font_access_for_user(data.get('font_id', ''), request.uid)
        harfler = _load_font_images(font_ref)
        secondary_harfler, secondary_font_data = _load_secondary_font(data, request.uid, data.get('font_id'))
        requested_settings = dict(data.get('page_settings') or {}) if isinstance(data.get('page_settings'), dict) else {}
        requested_settings['multi_author'] = bool(requested_settings.get('multi_author') and secondary_harfler)
        ui_language = _request_ui_language(data)
        source = str(data.get('source', 'ai'))
        if source == 'manual':
            if isinstance(data.get('blocks'), list):
                try:
                    blocks = _sanitize_client_copilot_blocks(data['blocks'])
                except _cop.CopilotError as exc:
                    raise ai_document.AiDocumentError(str(exc), exc.status_code) from exc
            else:
                blocks = ai_document.manual_blocks(
                    ai_document.normalize_text(data.get('text_content', '')),
                    ai_document.normalize_text(data.get('title', ''), maximum=180),
                )

            target_page_count = None
            try:
                proposed_target = int(requested_settings.get('target_page_count'))
                if 1 <= proposed_target <= ai_document.MAX_PAGES:
                    target_page_count = proposed_target
            except (TypeError, ValueError):
                pass

            fit_report = None
            if target_page_count:
                fit_result = ai_document.fit_layout_to_page_target(
                    blocks, harfler, requested_settings, target_page_count, secondary_harfler
                )
                fit_report = fit_result['report']
                if not fit_result['success']:
                    actual_pages = fit_report.get('actual_pages')
                    if fit_report.get('constraint') == 'underflow':
                        if ui_language == 'en':
                            question = (
                                f"Even with the most spacious readable layout, the edited text fills {actual_pages or 1} page(s). "
                                f"How should I spread it naturally across {target_page_count} pages?"
                            )
                            options = [
                                'Expand the text with examples and details (recommended)',
                                'Use larger letters and a more spacious layout',
                                f'Keep it at {actual_pages or 1} page(s)',
                                'I will adjust the measurements myself',
                            ]
                        else:
                            question = (
                                f"Düzenlediğin metin en ferah okunabilir ölçülerde {actual_pages or 1} sayfa oluyor. "
                                f"{target_page_count} sayfaya doğal biçimde yaymak için nasıl ilerleyelim?"
                            )
                            options = [
                                'Metni örnekler ve ayrıntılarla genişlet (önerilen)',
                                'Harfleri büyüt ve daha ferah bir düzen kullan',
                                f'{actual_pages or 1} sayfada bırak',
                                'Ölçüleri kendim ayarlayacağım',
                            ]
                    else:
                        if ui_language == 'en':
                            minimum_text = str(actual_pages) if actual_pages else f"more than {ai_document.MAX_PAGES}"
                            question = (
                                f"At the smallest readable size, the edited text needs {minimum_text} page(s). "
                                f"How should I proceed with the {target_page_count}-page target?"
                            )
                            options = [
                                'Shorten the text intelligently while preserving key information (recommended)',
                                'Remove the title and repetitions',
                                f'Increase the target to {actual_pages or min(ai_document.MAX_PAGES, target_page_count + 1)} pages',
                                'I will adjust the measurements myself',
                            ]
                        else:
                            minimum_text = str(actual_pages) if actual_pages else f"{ai_document.MAX_PAGES}'den fazla"
                            question = (
                                f"Düzenlediğin metin okunabilir en küçük ölçülerde {minimum_text} sayfa tutuyor. "
                                f"{target_page_count} sayfa hedefi için nasıl ilerleyelim?"
                            )
                            options = [
                                'Metni ana bilgileri koruyarak akıllıca kısalt (önerilen)',
                                'Başlığı ve tekrarları kaldır',
                                f'{actual_pages or min(ai_document.MAX_PAGES, target_page_count + 1)} sayfaya çıkar',
                                'Ölçüleri kendim ayarlayacağım',
                            ]
                    result = {
                        'needs_clarification': True,
                        'clarification_question': question,
                        'clarification_options': options,
                        'fit_report': fit_report,
                        'model': None,
                    }
                else:
                    layout = fit_result['layout']
                    blocks = fit_result['blocks']
                    requested_settings = fit_result['settings']
            else:
                layout = ai_document.build_layout(blocks, harfler, requested_settings, secondary_harfler)

            if not (target_page_count and fit_report and not fit_report.get('fits')):
                normalized_settings = ai_document.normalize_page_settings(requested_settings)
                updated_settings = {
                    'paper_type': normalized_settings['paper_type'],
                    'ink_color': normalized_settings['ink_color'],
                    'horizontal_align': normalized_settings['horizontal_align'],
                    'vertical_align': normalized_settings['vertical_align'],
                    'jitter': normalized_settings['jitter'],
                    'line_slope': normalized_settings['line_slope'],
                    'opacity': normalized_settings['opacity'],
                    'kalinlik': normalized_settings['kalinlik'],
                    'pen_dying_effect': normalized_settings['pen_dying_effect'],
                    'paper_age': normalized_settings['paper_age'],
                    'coffee_stains': normalized_settings['coffee_stains'],
                    'crease_effect': normalized_settings['crease_effect'],
                    'scale_jitter': normalized_settings['scale_jitter'],
                    'multi_author': normalized_settings['multi_author'] and bool(secondary_harfler),
                    'target_page_count': target_page_count,
                    **normalized_settings['units'],
                }
                summary = (
                    'The text was laid out using real font metrics.'
                    if ui_language == 'en' else
                    'Metin gerçek font ölçüleriyle mizanpajlandı.'
                )
                if fit_report:
                    units = fit_report['settings_after']
                    summary = (
                        (
                            f"The text was fitted to {fit_report['actual_pages']} page(s): "
                            f"letter height {units['letter_height_mm']:.2f} mm, "
                            f"line spacing {units['line_spacing_mm']:.2f} mm."
                        )
                        if ui_language == 'en' else
                        (
                            f"Metin {fit_report['actual_pages']} sayfaya sığdırıldı: "
                            f"harf {units['letter_height_mm']:.2f} mm, "
                            f"satır {units['line_spacing_mm']:.2f} mm."
                        )
                    )
                result = {
                    'needs_clarification': False,
                    'layout': layout,
                    'blocks': blocks,
                    'full_text': '\n\n'.join(block['text'] for block in blocks),
                    'summary': summary,
                    'font_profile': ai_document.font_profile(
                        harfler,
                        font_data.get('repetition', 1),
                        layout['settings']['letter_scale'],
                    ),
                    'model': None,
                    'updated_settings': updated_settings,
                    'fit_report': fit_report,
                }
        elif source == 'ai':
            provider_config = _ai_provider_config(os.environ.get(
                'AI_DOCUMENT_PROVIDER_ORDER', DEFAULT_AI_DOCUMENT_PROVIDER_ORDER
            ))
            result = ai_document.create_ai_layout(
                api_key=provider_config.get('gemini_key'),
                model=data.get('model', ai_document.DEFAULT_GEMINI_MODEL),
                template=str(data.get('template', 'odev')),
                topic=data.get('topic', ''),
                instructions=data.get('instructions', ''),
                harfler=harfler,
                repetition=font_data.get('repetition', 1),
                page_settings=requested_settings,
                secondary_harfler=secondary_harfler,
                secondary_repetition=(secondary_font_data or {}).get('repetition', 1),
                provider_config=provider_config,
                output_language=ui_language,
            )
        else:
            raise ai_document.AiDocumentError("source yalnızca 'ai' veya 'manual' olabilir.")
        return jsonify({
            'success': True,
            'font_name': font_data.get('font_name'),
            'secondary_font_name': (secondary_font_data or {}).get('font_name'),
            **result,
        })
    except Exception as exc:
        return _ai_error_response(exc)


@app.route('/api/font_chars_meta', methods=['GET'])
@verified_login_required
def font_chars_meta():
    try:
        font_ref, font_data = _font_access_for_user(request.args.get('font_id', ''), request.uid)
        harfler = _load_font_images(font_ref)
        profile = ai_document.font_profile(harfler, font_data.get('repetition', 1), 135)
        return jsonify({
            'success': True,
            'font_name': font_data.get('font_name', 'Bilinmiyor'),
            'char_count': len(harfler),
            'repetition': font_data.get('repetition', 1),
            'profile': profile,
        })
    except Exception as exc:
        return _ai_error_response(exc)


@app.route('/api/font_dimensions', methods=['GET'])
@verified_login_required
def font_dimensions():
    try:
        scale = int(request.args.get('letter_scale', 135))
        if not 50 <= scale <= 260:
            raise ai_document.AiDocumentError('letter_scale 50-260 arasında olmalı.')
        font_ref, font_data = _font_access_for_user(request.args.get('font_id', ''), request.uid)
        harfler = _load_font_images(font_ref)
        metrics = core_generator.get_font_metrics(harfler, scale)
        return jsonify({
            'success': True,
            'font_name': font_data.get('font_name'),
            'repetition': font_data.get('repetition', 1),
            'scale': scale,
            'page_width_px': ai_document.PAGE_WIDTH_PX,
            'page_height_px': ai_document.PAGE_HEIGHT_PX,
            'px_per_mm': ai_document.PX_PER_MM,
            'metrics': metrics,
        })
    except Exception as exc:
        return _ai_error_response(exc)


def _send_layout_pdf(layout, harfler, filename='fontify_belge.pdf', secondary_harfler=None):
    clean_layout = ai_document.validate_layout(layout)
    font_sets = {'secondary': secondary_harfler} if secondary_harfler else None
    pages = core_generator.metni_koordinatli_yaz(clean_layout, harfler, font_sets)
    pdf_buffer = core_generator.sayfalari_pdf_olustur(pages)
    if pdf_buffer is None:
        raise ai_document.AiDocumentError('PDF sayfası oluşturulamadı.', 422)
    response = send_file(
        pdf_buffer,
        mimetype='application/pdf',
        as_attachment=False,
        download_name=filename,
        max_age=0,
    )
    response.headers['X-Fontify-Pages'] = str(len(clean_layout.get('pages', [])))
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.route('/api/ai_layout_pdf', methods=['POST', 'OPTIONS'])
@verified_login_required
def ai_layout_pdf():
    try:
        if request.content_length and request.content_length > 2 * 1024 * 1024:
            raise ai_document.AiDocumentError('Layout isteği en fazla 2 MB olabilir.', 413)
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ai_document.AiDocumentError('Geçerli bir JSON gövdesi gerekli.')
        font_ref, _ = _font_access_for_user(data.get('font_id', ''), request.uid)
        secondary_harfler, _ = _load_secondary_font(data, request.uid, data.get('font_id'))
        return _send_layout_pdf(
            data.get('layout'),
            _load_font_images(font_ref),
            'fontify_ai_belge.pdf',
            secondary_harfler,
        )
    except Exception as exc:
        return _ai_error_response(exc)


@app.route('/api/ai_generate_pdf', methods=['POST', 'OPTIONS'])
@verified_login_required
def ai_generate_pdf():
    """Backward-compatible manual text renderer using the safe layout engine."""
    try:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ai_document.AiDocumentError('Geçerli bir JSON gövdesi gerekli.')
        font_ref, _ = _font_access_for_user(data.get('font_id', ''), request.uid)
        harfler = _load_font_images(font_ref)
        secondary_harfler, _ = _load_secondary_font(data, request.uid, data.get('font_id'))
        blocks = ai_document.manual_blocks(ai_document.normalize_text(data.get('text_content', '')))
        layout = ai_document.build_layout(blocks, harfler, data.get('page_settings'), secondary_harfler)
        overrides = data.get('per_line_overrides') if isinstance(data.get('per_line_overrides'), dict) else {}
        flat_lines = [line for page in layout['pages'] for line in page['lines']]
        for key, values in overrides.items():
            try:
                target = flat_lines[int(key)]
            except (ValueError, IndexError, TypeError):
                continue
            if not isinstance(values, dict):
                continue
            for field in ('letter_scale', 'letter_spacing', 'word_spacing', 'line_slope', 'jitter', 'scale_jitter', 'ink_color', 'opacity', 'kalinlik', 'font_slot', 'line_offset_y'):
                if field in values:
                    target[field] = values[field]
        return _send_layout_pdf(layout, harfler, secondary_harfler=secondary_harfler)
    except Exception as exc:
        return _ai_error_response(exc)

# ─── Fontify Copilot Engine endpoints ────────────────────────────────────────

import ai_copilot as _cop
import copilot_store as _store
from flask import Response, stream_with_context

MAX_COPILOT_HISTORY = 30
MAX_COPILOT_DOCS = 500

def _get_copilot_doc(document_id: str, user_id: str) -> dict:
    try:
        return _store.get_document(document_id, user_id)
    except _store.CopilotStoreError as e:
        raise _cop.CopilotError(str(e), e.status_code)
    except Exception as e:
        logger.error("Firestore document load failed: %s", type(e).__name__, exc_info=True)
        raise _cop.CopilotError("Belge depolama hizmeti şu anda kullanılamıyor.", 503)

def _copilot_error_response(exc: Exception):
    if isinstance(exc, _cop.CopilotError):
        return jsonify({"success": False, "message": str(exc)}), exc.status_code
    if isinstance(exc, _store.CopilotStoreError):
        return jsonify({"success": False, "message": str(exc)}), exc.status_code
    if isinstance(exc, ai_document.AiDocumentError):
        return jsonify({"success": False, "message": str(exc)}), exc.status_code
    logger.error("Copilot error: %s", type(exc).__name__, exc_info=True)
    return jsonify({"success": False, "message": "Copilot işlemi tamamlanamadı."}), 500

def _sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _stored_page_target(settings: Any) -> int | None:
    if not isinstance(settings, dict):
        return None
    try:
        target = int(settings.get("target_page_count"))
    except (TypeError, ValueError):
        return None
    return target if 1 <= target <= ai_document.MAX_PAGES else None


def _canonical_page_settings(
    value: Any,
    fallback: Any = None,
    *,
    preserve_missing_target: bool = True,
) -> dict:
    """Normalize persisted page settings and retain an active page target safely."""
    source = value if isinstance(value, dict) else {}
    normalized = ai_document.normalize_page_settings(source)
    # Firestore/API state deliberately uses the same flat, millimetric shape
    # as the frontend. ``normalize_page_settings`` is an internal pixel layout
    # representation, so persisting it directly would lose mm controls on the
    # next reflow.
    settings = {
        "paper_type": normalized["paper_type"],
        "ink_color": normalized["ink_color"],
        "horizontal_align": normalized["horizontal_align"],
        "vertical_align": normalized["vertical_align"],
        "jitter": normalized["jitter"],
        "line_slope": normalized["line_slope"],
        "opacity": normalized["opacity"],
        "kalinlik": normalized["kalinlik"],
        "pen_dying_effect": normalized["pen_dying_effect"],
        "paper_age": normalized["paper_age"],
        "coffee_stains": normalized["coffee_stains"],
        "crease_effect": normalized["crease_effect"],
        "scale_jitter": normalized["scale_jitter"],
        "multi_author": normalized["multi_author"],
        **normalized["units"],
    }
    target_source = source
    if (
        preserve_missing_target
        and "target_page_count" not in source
        and isinstance(fallback, dict)
    ):
        target_source = fallback
    target = _stored_page_target(target_source)
    if target:
        settings["target_page_count"] = target
    return settings


def _effective_page_settings(result: dict, requested: Any, current: Any) -> dict:
    """Choose the exact settings snapshot that will be persisted with an edit."""
    result_settings = result.get("page_settings_update")
    if isinstance(result_settings, dict):
        # A result snapshot can intentionally remove target_page_count after a
        # user chooses manual measurements, so do not inherit it here.
        return _canonical_page_settings(
            result_settings, current, preserve_missing_target=False
        )

    merged = dict(current) if isinstance(current, dict) else {}
    if isinstance(requested, dict):
        merged.update(requested)

    new_layout = result.get("new_layout")
    layout_settings = (
        new_layout.get("settings")
        if isinstance(new_layout, dict) and isinstance(new_layout.get("settings"), dict)
        else None
    )
    if isinstance(layout_settings, dict):
        units = layout_settings.get("units")
        if isinstance(units, dict):
            merged.update(units)
        for key in (
            "paper_type", "ink_color", "horizontal_align", "vertical_align",
            "jitter", "line_slope", "opacity", "kalinlik", "pen_dying_effect",
            "paper_age", "coffee_stains", "crease_effect", "scale_jitter",
            "multi_author", "letter_height_mm", "line_spacing_mm",
            "letter_spacing_mm", "word_spacing_mm", "margin_top_mm",
            "margin_bottom_mm", "margin_left_mm", "margin_right_mm",
            "target_page_count",
        ):
            if key in layout_settings:
                merged[key] = layout_settings[key]
    return _canonical_page_settings(merged, current)


def _sanitize_client_copilot_blocks(value: Any) -> list[dict]:
    """Canonicalize browser-supplied blocks before they can become server state."""
    if not isinstance(value, list) or len(value) > ai_document.MAX_BLOCKS:
        raise _cop.CopilotError(f"En fazla {ai_document.MAX_BLOCKS} blok.")
    cleaned: list[dict] = []
    for raw_block in value:
        if not isinstance(raw_block, dict):
            continue
        try:
            normalized = ai_document.sanitize_blocks([raw_block])[0]
        except (ai_document.AiDocumentError, IndexError):
            continue
        candidate_id = str(raw_block.get("id") or "").strip()
        if _cop.DOCUMENT_ID_RE.fullmatch(candidate_id):
            normalized["id"] = candidate_id
        target_page_id = str(raw_block.get("target_page_id") or "").strip()
        if normalized.get("is_margin_note") and _cop.DOCUMENT_ID_RE.fullmatch(target_page_id):
            normalized["target_page_id"] = target_page_id
        style_patch = _cop._sanitize_patch(
            raw_block,
            _cop.ALLOWED_BLOCK_STYLE_FIELDS,
        )
        normalized.update(style_patch)
        cleaned.append(normalized)
    if not cleaned:
        raise _cop.CopilotError("Güncel belge blokları geçersiz.")
    return cleaned


def _copilot_full_text(blocks: Any) -> str:
    if not isinstance(blocks, list):
        return ""
    return "\n".join(
        str(block.get("text") or "")
        for block in blocks
        if isinstance(block, dict) and str(block.get("text") or "").strip()
    )[:ai_document.MAX_DOCUMENT_CHARS]


_COPILOT_LINE_OVERRIDE_BASE = "_copilot_override_base"
_COPILOT_LINE_LOCATOR = "_line_locator"
_PERSISTED_LINE_OVERRIDE_FIELDS = frozenset(
    set(_cop.ALLOWED_LINE_STYLE_FIELDS) | {"start_x", "baseline_y"}
)


def _lines_for_copilot_block(layout: dict, block_id: str) -> list[dict]:
    return [
        line
        for page in layout.get("pages", [])
        if isinstance(page, dict)
        for line in page.get("lines", [])
        if isinstance(line, dict) and str(line.get("block_id") or "") == block_id
    ]


def _copilot_line_locator(layout: dict, target_id: Any) -> dict | None:
    line = _cop._get_line_by_id(layout, target_id)
    if line is None:
        return None
    block_id = str(line.get("block_id") or "")
    if not block_id:
        return None
    block_lines = _lines_for_copilot_block(layout, block_id)
    try:
        ordinal = next(
            index for index, candidate in enumerate(block_lines)
            if candidate is line or candidate.get("id") == line.get("id")
        )
    except StopIteration:
        return None
    return {"block_id": block_id, "ordinal": ordinal}


def _copilot_line_by_locator(layout: dict, locator: Any) -> dict | None:
    if not isinstance(locator, dict):
        return None
    block_id = str(locator.get("block_id") or "")
    try:
        ordinal = int(locator.get("ordinal"))
    except (TypeError, ValueError):
        return None
    if not block_id or ordinal < 0:
        return None
    block_lines = _lines_for_copilot_block(layout, block_id)
    return block_lines[ordinal] if ordinal < len(block_lines) else None


def _line_operation_fields(operation: dict) -> set[str]:
    name = operation.get("operation")
    if name == "move_line":
        return {"start_x", "baseline_y"}
    if name == "switch_line_author":
        return {"font_slot"}
    if name == "update_line_style":
        patch = operation.get("patch")
        if isinstance(patch, dict):
            return set(patch).intersection(_PERSISTED_LINE_OVERRIDE_FIELDS)
    return set()


def _sync_line_override_metadata(
    before_layout: dict,
    patched_layout: dict,
    operations: Any,
) -> None:
    """Persist semantic line overrides without tying them to volatile line IDs.

    Reflow regenerates line IDs and coordinates. The override base is carried on
    the line itself, while block id + ordinal is used to move it to the matching
    regenerated line. Keeping the original base also lets undo remove an
    override instead of accidentally pinning a former default forever.
    """
    if not isinstance(operations, list):
        return
    for operation in operations:
        if not isinstance(operation, dict):
            continue
        fields = _line_operation_fields(operation)
        if not fields:
            continue
        locator = operation.get(_COPILOT_LINE_LOCATOR)
        if not isinstance(locator, dict):
            locator = _copilot_line_locator(before_layout, operation.get("target_id"))
        if not locator:
            continue
        before_line = _copilot_line_by_locator(before_layout, locator)
        patched_line = _copilot_line_by_locator(patched_layout, locator)
        if before_line is None or patched_line is None:
            continue
        raw_base = patched_line.get(_COPILOT_LINE_OVERRIDE_BASE)
        if not isinstance(raw_base, dict):
            raw_base = before_line.get(_COPILOT_LINE_OVERRIDE_BASE)
        base = {
            key: value
            for key, value in (raw_base.items() if isinstance(raw_base, dict) else [])
            if key in _PERSISTED_LINE_OVERRIDE_FIELDS
        }
        for field in fields:
            original = base[field] if field in base else before_line.get(field)
            current = patched_line.get(field)
            if current == original:
                base.pop(field, None)
            else:
                base[field] = original
        if base:
            patched_line[_COPILOT_LINE_OVERRIDE_BASE] = base
        else:
            patched_line.pop(_COPILOT_LINE_OVERRIDE_BASE, None)


def _capture_manual_line_override_metadata(
    before_layout: dict,
    client_layout: dict,
) -> None:
    """Retain inspector-authored line changes across future AI reflows.

    The state endpoint receives a complete layout rather than semantic
    operations. Compare each regenerated-safe line by block id + ordinal and
    record only fields whose effective value changed. Existing bases are kept,
    so manually restoring the original value also clears the override.
    """
    block_ordinals: dict[str, int] = {}
    for page in client_layout.get("pages", []):
        if not isinstance(page, dict):
            continue
        for client_line in page.get("lines", []):
            if not isinstance(client_line, dict):
                continue
            block_id = str(client_line.get("block_id") or "")
            ordinal = block_ordinals.get(block_id, 0)
            block_ordinals[block_id] = ordinal + 1
            locator = {"block_id": block_id, "ordinal": ordinal}
            before_line = _copilot_line_by_locator(before_layout, locator)
            if before_line is None:
                continue
            raw_base = before_line.get(_COPILOT_LINE_OVERRIDE_BASE)
            base = {
                key: value
                for key, value in (raw_base.items() if isinstance(raw_base, dict) else [])
                if key in _PERSISTED_LINE_OVERRIDE_FIELDS
            }
            for field in _PERSISTED_LINE_OVERRIDE_FIELDS:
                previous = before_line.get(field)
                current = client_line.get(field)
                if current == previous:
                    continue
                original = base[field] if field in base else previous
                if current == original:
                    base.pop(field, None)
                else:
                    base[field] = original
            if base:
                client_line[_COPILOT_LINE_OVERRIDE_BASE] = base
            else:
                client_line.pop(_COPILOT_LINE_OVERRIDE_BASE, None)


def _reapply_persisted_line_overrides(rebuilt: dict, patched_layout: dict) -> None:
    """Map every previously saved line override onto regenerated lines."""
    for page in patched_layout.get("pages", []):
        if not isinstance(page, dict):
            continue
        for source in page.get("lines", []):
            if not isinstance(source, dict):
                continue
            raw_base = source.get(_COPILOT_LINE_OVERRIDE_BASE)
            if not isinstance(raw_base, dict) or not raw_base:
                continue
            locator = _copilot_line_locator(patched_layout, source.get("id"))
            target = _copilot_line_by_locator(rebuilt, locator)
            if target is None:
                continue
            clean_base = {
                key: value
                for key, value in raw_base.items()
                if key in _PERSISTED_LINE_OVERRIDE_FIELDS
            }
            for field in clean_base:
                if field in source:
                    target[field] = source[field]
                else:
                    target.pop(field, None)
            if clean_base:
                target[_COPILOT_LINE_OVERRIDE_BASE] = clean_base


def _annotate_line_operation_locators(layout: dict, operations: Any) -> None:
    """Attach a stable locator to history operations for later undo/redo."""
    if not isinstance(operations, list):
        return
    for operation in operations:
        if not isinstance(operation, dict) or not _line_operation_fields(operation):
            continue
        locator = _copilot_line_locator(layout, operation.get("target_id"))
        if locator:
            operation[_COPILOT_LINE_LOCATOR] = locator


def _remap_line_operation_targets(layout: dict, operations: Any) -> Any:
    """Resolve history line targets after unrelated wrapping changed line IDs."""
    if not isinstance(operations, list):
        return operations
    remapped = copy.deepcopy(operations)
    for operation in remapped:
        if not isinstance(operation, dict) or not _line_operation_fields(operation):
            continue
        target = _copilot_line_by_locator(layout, operation.get(_COPILOT_LINE_LOCATOR))
        if target is not None and target.get("id"):
            operation["target_id"] = target["id"]
    return remapped


def _reapply_targeted_line_operations(
    rebuilt: dict,
    patched_layout: dict,
    operations: Any,
) -> None:
    """Keep the current line edit visible when a document reflow is required."""
    if not isinstance(operations, list):
        return

    for operation in operations:
        if not isinstance(operation, dict):
            continue
        name = operation.get("operation")
        if name not in {"update_line_style", "move_line", "switch_line_author"}:
            continue
        source = _cop._get_line_by_id(patched_layout, operation.get("target_id"))
        if source is None:
            continue
        block_id = str(source.get("block_id") or "")
        if not block_id:
            continue
        source_lines = _lines_for_copilot_block(patched_layout, block_id)
        target_lines = _lines_for_copilot_block(rebuilt, block_id)
        try:
            ordinal = next(i for i, line in enumerate(source_lines) if line.get("id") == source.get("id"))
        except StopIteration:
            continue
        if ordinal >= len(target_lines):
            continue
        target = target_lines[ordinal]
        if name == "move_line":
            target["start_x"] = source.get("start_x", target.get("start_x"))
            target["baseline_y"] = source.get("baseline_y", target.get("baseline_y"))
        else:
            patch = operation.get("patch") or {}
            for key in patch:
                if key in _cop.ALLOWED_LINE_STYLE_FIELDS and key in source:
                    target[key] = source[key]
            if name == "switch_line_author":
                target["font_slot"] = source.get("font_slot", target.get("font_slot", "primary"))


def _copilot_reflow_state(
    doc: dict, layout: dict, blocks: list, version: int,
    target_pages: int | None = None,
    operations: Any = None,
) -> tuple[dict, list, dict | None]:
    """Rebuild wrapped lines from edited blocks using the document's real fonts."""
    font_id = str(doc.get("font_id") or "").strip()
    if not font_id:
        raise _cop.CopilotError(
            "Bu eski Copilot oturumunda font bilgisi eksik. Belgeyi bir kez yeniden oluşturun.",
            409,
        )

    font_ref, _ = _font_access_for_user(font_id, doc["user_id"])
    harfler = _load_font_images(font_ref)
    secondary_harfler = None
    secondary_id = str(doc.get("secondary_font_id") or "").strip()
    if secondary_id:
        secondary_ref, _ = _font_access_for_user(secondary_id, doc["user_id"])
        secondary_harfler = _load_font_images(secondary_ref)

    raw_settings = dict(doc.get("page_settings") or {})
    patched_settings = layout.get("settings") if isinstance(layout.get("settings"), dict) else {}
    for key in _cop.ALLOWED_DOC_SETTINGS_FIELDS:
        if key in patched_settings:
            raw_settings[key] = patched_settings[key]

    # A document-level Copilot operation updates layout.settings immediately,
    # while the existing page objects still contain the pre-edit values. Do not
    # let that stale first-page snapshot overwrite the requested global edit
    # during the rebuild (this previously made slope/margins/typography appear
    # to succeed in chat but disappear from the PDF).
    global_setting_fields = {
        key
        for operation in (operations or [])
        if isinstance(operation, dict)
        and operation.get("operation") == "update_document_settings"
        and isinstance(operation.get("patch"), dict)
        for key in operation["patch"]
        if key in _cop.ALLOWED_DOC_SETTINGS_FIELDS
    }

    patched_pages = layout.get("pages") if isinstance(layout.get("pages"), list) else []
    if patched_pages:
        first_page = patched_pages[0]
        for px_key, mm_key in (
            ("margin_top", "margin_top_mm"),
            ("margin_bottom", "margin_bottom_mm"),
            ("margin_left", "margin_left_mm"),
            ("margin_right", "margin_right_mm"),
            ("line_spacing", "line_spacing_mm"),
        ):
            if px_key in first_page and mm_key not in global_setting_fields:
                raw_settings[mm_key] = ai_document.px_to_mm(first_page[px_key])
        for key in (
            "paper_type", "paper_age", "coffee_stains", "crease_effect",
            "pen_dying_effect", "opacity", "kalinlik", "scale_jitter", "multi_author",
        ):
            if key in first_page and key not in global_setting_fields:
                raw_settings[key] = first_page[key]

    fit_result = None
    if target_pages:
        fit_result = ai_document.fit_layout_to_page_target(
            blocks, harfler, raw_settings, target_pages, secondary_harfler
        )
        rebuilt = fit_result.get("layout")
        if rebuilt is None:
            rebuilt = layout
        blocks = fit_result.get("blocks") or blocks
        if fit_result.get("success"):
            raw_settings = fit_result["settings"]
    else:
        rebuilt = ai_document.build_layout(blocks, harfler, raw_settings, secondary_harfler)
    rebuilt["version"] = version

    # Keep millimetric typography values at the canonical layout root. This is
    # what makes target-page fitting survive later edits, undo/redo and restores.
    if fit_result and fit_result.get("success"):
        rebuilt.setdefault("settings", {}).update(fit_result["report"]["settings_after"])

    # Preserve page-specific visual overrides that do not alter text wrapping.
    visual_fields = (
        "paper_type", "paper_age", "coffee_stains", "crease_effect",
        "pen_dying_effect", "opacity", "kalinlik", "scale_jitter", "multi_author",
        "ink_color", "jitter", "line_slope",
    )
    for index, page in enumerate(rebuilt.get("pages", [])):
        if index >= len(patched_pages):
            break
        for key in visual_fields:
            if key in patched_pages[index]:
                page[key] = patched_pages[index][key]
        line_visual_patch = {
            key: patched_pages[index][key]
            for key in _cop.PAGE_LINE_VISUAL_FIELDS
            if key in patched_pages[index]
        }
        if line_visual_patch:
            for line in page.get("lines", []):
                line.update(line_visual_patch)

    # Apply durable overrides first, then the current operation as a final
    # compatibility pass. This keeps old stored documents working while new
    # edits gain stable block+ordinal persistence.
    _reapply_persisted_line_overrides(rebuilt, layout)
    _reapply_targeted_line_operations(rebuilt, layout, operations)

    rebuilt, blocks = _cop.ensure_document_ids(rebuilt, blocks)
    return rebuilt, blocks, fit_result


def _finalize_copilot_result(
    doc: dict,
    result: dict,
    source_layout: dict | None = None,
    ui_language: str = "tr",
) -> dict:
    """Ensure edited block text is actually rewrapped before state is committed."""
    if result.get("needs_clarification"):
        return result

    operation_base = source_layout if isinstance(source_layout, dict) else doc.get("layout")
    if isinstance(operation_base, dict):
        _sync_line_override_metadata(
            operation_base,
            result.get("new_layout") or {},
            result.get("operations"),
        )

    page_target_intent = str(result.get("page_target_intent") or "")
    if page_target_intent == "manual":
        target_pages = None
    elif page_target_intent == "exact":
        target_pages = _stored_page_target({"target_page_count": result.get("target_page_count")})
    else:
        target_pages = _stored_page_target(doc.get("page_settings"))

    if page_target_intent == "manual":
        cleared_settings = dict(doc.get("page_settings") or {})
        cleared_settings.pop("target_page_count", None)
        result["page_settings_update"] = cleared_settings
        if isinstance(result.get("new_layout"), dict):
            result["new_layout"].setdefault("settings", {}).pop("target_page_count", None)

    if result.get("reflow_needed") or target_pages:
        layout, blocks, fit_result = _copilot_reflow_state(
            doc,
            result["new_layout"],
            result["new_blocks"],
            int(result["new_layout"].get("version", doc["version"] + 1)),
            target_pages,
            result.get("operations"),
        )
        if fit_result and not fit_result.get("success"):
            report = fit_result["report"]
            actual_pages = report.get("actual_pages")
            english = ui_language == "en"
            if report.get("constraint") == "underflow":
                if english:
                    question = (
                        f"Even with the most spacious readable layout, this text fills "
                        f"{actual_pages or 1} page(s). How should I spread it naturally "
                        f"across {target_pages} pages?"
                    )
                    options = [
                        "Expand the text with examples and details (recommended)",
                        "Use larger letters and a more spacious layout",
                        f"Keep it at {actual_pages or 1} page(s)",
                        "I will adjust the measurements myself",
                    ]
                else:
                    question = (
                        f"Bu metin en ferah okunabilir düzende bile {actual_pages or 1} sayfa oluyor. "
                        f"{target_pages} sayfaya doğal biçimde yaymak için nasıl ilerleyeyim?"
                    )
                    options = [
                        "Metni örnekler ve ayrıntılarla genişlet (önerilen)",
                        "Harfleri büyüt ve daha ferah bir düzen kullan",
                        f"{actual_pages or 1} sayfada bırak",
                        "Ölçüleri kendim ayarlayacağım",
                    ]
            else:
                if english:
                    minimum_text = str(actual_pages) if actual_pages else f"more than {ai_document.MAX_PAGES}"
                    question = (
                        f"At the smallest readable size, this text needs {minimum_text} page(s). "
                        f"How should I proceed with the {target_pages}-page target?"
                    )
                    options = [
                        "Shorten the text intelligently while preserving key information (recommended)",
                        "Remove the title and repetitions",
                        f"Increase the target to {actual_pages or min(ai_document.MAX_PAGES, int(target_pages) + 1)} pages",
                        "I will adjust the measurements myself",
                    ]
                else:
                    minimum_text = str(actual_pages) if actual_pages else f"{ai_document.MAX_PAGES}'den fazla"
                    question = (
                        f"Bu metin okunabilir en küçük ölçülerde {minimum_text} sayfa tutuyor. "
                        f"{target_pages} sayfa hedefi için nasıl ilerleyeyim?"
                    )
                    options = [
                        "Metni ana bilgileri koruyarak akıllıca kısalt (önerilen)",
                        "Başlığı ve tekrarları kaldır",
                        f"{actual_pages or min(ai_document.MAX_PAGES, int(target_pages) + 1)} sayfaya çıkar",
                        "Ölçüleri kendim ayarlayacağım",
                    ]
            result.update({
                "needs_clarification": True,
                "clarification_question": question,
                "clarification_options": options,
                "assistant_message": (
                    "I measured the target and am waiting for your choice without sacrificing readability."
                    if english else
                    "Hedefi ölçtüm; okunabilirliği bozmadan kararını bekliyorum."
                ),
                "fit_report": report,
                "operations": [],
                "inverse_operations": [],
                "reflow_needed": False,
            })
            return result

        if fit_result:
            report = fit_result["report"]
            after = report["settings_after"]
            before = report["settings_before"]
            doc_fields = ("letter_height_mm", "line_spacing_mm", "letter_spacing_mm", "word_spacing_mm")
            page_field_map = {
                "margin_top": "margin_top_mm", "margin_bottom": "margin_bottom_mm",
                "margin_left": "margin_left_mm", "margin_right": "margin_right_mm",
                "line_spacing": "line_spacing_mm",
            }
            prior_breaks = {
                str(block.get("id")): bool(block.get("page_break_before", False))
                for block in result.get("new_blocks", [])
                if isinstance(block, dict) and block.get("id")
            }
            changed_breaks = [
                {"id": str(block.get("id")), "page_break_before": bool(block.get("page_break_before", False))}
                for block in blocks
                if isinstance(block, dict)
                and block.get("id")
                and prior_breaks.get(str(block.get("id")), False) != bool(block.get("page_break_before", False))
            ]
            previous_breaks = [
                {"id": entry["id"], "page_break_before": prior_breaks[entry["id"]]}
                for entry in changed_breaks
            ]
            fit_operations = [{
                "operation": "update_document_settings",
                "patch": {key: after[key] for key in doc_fields},
            }, {
                "operation": "update_page_settings",
                "target_id": "",
                "patch": {
                    px_key: int(round(after[mm_key] * ai_document.PX_PER_MM))
                    for px_key, mm_key in page_field_map.items()
                },
            }]
            if changed_breaks:
                fit_operations.append({"operation": "restore_block_page_breaks", "page_breaks": changed_breaks})

            fit_inverse = [{
                "operation": "update_page_settings",
                "target_id": "",
                "patch": {
                    px_key: int(round(before[mm_key] * ai_document.PX_PER_MM))
                    for px_key, mm_key in page_field_map.items()
                },
            }, {
                "operation": "update_document_settings",
                "patch": {key: before[key] for key in doc_fields},
            }]
            if previous_breaks:
                fit_inverse.append({"operation": "restore_block_page_breaks", "page_breaks": previous_breaks})
            result["operations"] = list(result.get("operations") or []) + fit_operations
            result["inverse_operations"] = fit_inverse + list(result.get("inverse_operations") or [])
            result["fit_report"] = report
            persisted_settings = dict(fit_result["settings"])
            persisted_settings["target_page_count"] = target_pages
            result["page_settings_update"] = persisted_settings
            layout.setdefault("settings", {})["target_page_count"] = target_pages
            result["reflow_needed"] = True
            result["assistant_message"] = (
                (
                    f"I fit the document to {report['actual_pages']} page(s) using real font metrics: "
                    f"letters {after['letter_height_mm']:.2f} mm, lines {after['line_spacing_mm']:.2f} mm, "
                    f"word spacing {after['word_spacing_mm']:.2f} mm."
                )
                if ui_language == "en" else
                (
                    f"Belgeyi {report['actual_pages']} sayfaya gerçek font ölçüleriyle sığdırdım: "
                    f"harf {after['letter_height_mm']:.2f} mm, satır {after['line_spacing_mm']:.2f} mm, "
                    f"kelime aralığı {after['word_spacing_mm']:.2f} mm."
                )
            )
        result["new_layout"] = layout
        result["new_blocks"] = blocks
    else:
        layout, blocks = _cop.ensure_document_ids(result["new_layout"], result["new_blocks"])
        result["new_layout"] = layout
        result["new_blocks"] = blocks
    if isinstance(operation_base, dict):
        _annotate_line_operation_locators(operation_base, result.get("operations"))
        _annotate_line_operation_locators(operation_base, result.get("inverse_operations"))
    return result

@app.route("/api/ai/documents", methods=["POST", "OPTIONS"])
@verified_login_required
def copilot_save_document():
    if request.method == 'OPTIONS':
        return '', 204
    try:
        data = request.get_json(silent=True) or {}
        layout = data.get("layout")
        blocks = data.get("blocks")
        if not isinstance(layout, dict) or not isinstance(blocks, list):
            raise _cop.CopilotError("layout ve blocks gerekli.")
        if len(json.dumps(layout)) > 2_000_000:
            raise _cop.CopilotError("Layout çok büyük.")
        if len(blocks) > ai_document.MAX_BLOCKS:
            raise _cop.CopilotError(f"En fazla {ai_document.MAX_BLOCKS} blok.")

        # A browser payload is untrusted even when it originates from our own
        # editor. Canonicalize it before it becomes the durable Copilot base.
        raw_page_settings = data.get("page_settings") if isinstance(data.get("page_settings"), dict) else {}
        try:
            source_version = int(layout.get("version", 1))
        except (TypeError, ValueError) as exc:
            raise _cop.CopilotError("Geçerli bir belge sürümü gerekli.") from exc
        layout = ai_document.validate_layout(layout)
        layout["version"] = source_version
        persisted_page_settings = _canonical_page_settings(
            raw_page_settings, preserve_missing_target=False
        )
        layout["settings"] = dict(persisted_page_settings)
        blocks = _sanitize_client_copilot_blocks(blocks)
        layout, blocks = _cop.ensure_document_ids(layout, blocks)
        font_id = str(data.get("font_id") or "").strip()
        secondary_font_id = str(data.get("secondary_font_id") or "").strip()
        if font_id:
            _font_access_for_user(font_id, request.uid)
        if secondary_font_id:
            _font_access_for_user(secondary_font_id, request.uid)
            if secondary_font_id == font_id:
                raise _cop.CopilotError("İkinci font birinci fonttan farklı olmalı.")
        version = int(layout.get("version", 1))
        document_id = _store.create_document(
            user_id=request.uid,
            font_id=font_id,
            secondary_font_id=secondary_font_id,
            page_settings=persisted_page_settings,
            version=version,
            layout=layout,
            blocks=blocks
        )
        return jsonify({
            "success": True,
            "document_id": document_id,
            "version": version,
            "layout": layout,
            "blocks": blocks,
        })
    except Exception as exc:
        return _copilot_error_response(exc)

@app.route("/api/ai/documents/latest", methods=["GET", "OPTIONS"])
@verified_login_required
def copilot_get_latest_document():
    if request.method == 'OPTIONS':
        return '', 204
    try:
        doc = _store.get_latest_document(request.uid)
        if not doc:
            raise _cop.CopilotError("Belge bulunamadı.", 404)
        page_hashes = {
            page.get("id", f"p{i}"): _cop.layout_page_hash(page)
            for i, page in enumerate(doc["layout"].get("pages", []))
        }
        return jsonify({
            "success": True,
            "document_id": doc["id"],
            "version": doc["version"],
            "font_id": doc.get("font_id", ""),
            "secondary_font_id": doc.get("secondary_font_id", ""),
            "page_settings": doc.get("page_settings", {}),
            "layout": doc["layout"],
            "blocks": doc["blocks"],
            "full_text": _copilot_full_text(doc["blocks"]),
            "page_hashes": page_hashes,
            "can_undo": len(doc.get("history", [])) > 0,
            "can_redo": len(doc.get("redo_stack", [])) > 0,
        })
    except _store.CopilotStoreError as e:
        return jsonify({"success": False, "message": str(e)}), e.status_code
    except _cop.CopilotError as e:
        return jsonify({"success": False, "message": str(e)}), e.status_code
    except Exception as exc:
        return _copilot_error_response(exc)

@app.route("/api/ai/documents/<document_id>", methods=["GET", "OPTIONS"])
@verified_login_required
def copilot_get_document(document_id: str):
    if request.method == 'OPTIONS':
        return '', 204
    try:
        doc = _get_copilot_doc(document_id, request.uid)
        page_hashes = {
            page.get("id", f"p{i}"): _cop.layout_page_hash(page)
            for i, page in enumerate(doc["layout"].get("pages", []))
        }
        return jsonify({
            "success": True,
            "version": doc["version"],
            "document_id": doc["id"],
            "font_id": doc.get("font_id", ""),
            "secondary_font_id": doc.get("secondary_font_id", ""),
            "page_settings": doc.get("page_settings", {}),
            "layout": doc["layout"],
            "blocks": doc["blocks"],
            "full_text": _copilot_full_text(doc["blocks"]),
            "page_hashes": page_hashes,
            "can_undo": len(doc["history"]) > 0,
            "can_redo": len(doc["redo_stack"]) > 0,
        })
    except Exception as exc:
        return _copilot_error_response(exc)

@app.route("/api/ai/documents/<document_id>/state", methods=["PATCH", "OPTIONS"])
@verified_login_required
def copilot_save_manual_state(document_id: str):
    """Durably save direct inspector edits without replaying stale AI history."""
    if request.method == "OPTIONS":
        return "", 204
    try:
        data = request.get_json(silent=True) or {}
        doc = _get_copilot_doc(document_id, request.uid)
        try:
            client_version = int(data.get("document_version"))
        except (TypeError, ValueError) as exc:
            raise _cop.CopilotError("Belge sürümü gerekli.") from exc
        if client_version != doc["version"]:
            raise _cop.VersionConflictError()
        client_layout = data.get("layout")
        client_blocks = data.get("blocks")
        if not isinstance(client_layout, dict) or not isinstance(client_blocks, list):
            raise _cop.CopilotError("Güncel layout ve bloklar gerekli.")
        if len(json.dumps(client_layout, ensure_ascii=False)) > 2_000_000:
            raise _cop.CopilotError("Güncel layout çok büyük.", 413)
        layout = ai_document.validate_layout(client_layout)
        _capture_manual_line_override_metadata(doc["layout"], layout)
        blocks = _sanitize_client_copilot_blocks(client_blocks)
        supplied_page_settings = data.get("page_settings")
        if not isinstance(supplied_page_settings, dict):
            supplied_page_settings = dict(doc.get("page_settings") or {})
        page_settings = _canonical_page_settings(
            supplied_page_settings, doc.get("page_settings")
        )
        stored_target = _stored_page_target(page_settings)

        # Layout-level settings are server-owned state. Inspector edits only
        # change page settings, blocks, and page geometry sent by the client.
        layout["settings"] = dict((doc.get("layout") or {}).get("settings") or {})
        if stored_target:
            layout["settings"]["target_page_count"] = stored_target
        else:
            layout["settings"].pop("target_page_count", None)
        layout, blocks = _cop.ensure_document_ids(layout, blocks)
        layout["version"] = doc["version"] + 1
        new_version = _store.save_manual_state(
            document_id, request.uid, doc["version"], layout, blocks, page_settings
        )
        page_hashes = {
            page.get("id", f"p{i}"): _cop.layout_page_hash(page)
            for i, page in enumerate(layout.get("pages", []))
        }
        return jsonify({
            "success": True,
            "version": new_version,
            "new_layout": layout,
            "new_blocks": blocks,
            "page_settings": page_settings,
            "page_hashes": page_hashes,
            "can_undo": False,
            "can_redo": False,
        })
    except Exception as exc:
        return _copilot_error_response(exc)


@app.route("/api/ai/documents/<document_id>/edits", methods=["POST", "OPTIONS"])
@verified_login_required
def copilot_edit_document(document_id: str):
    if request.method == 'OPTIONS':
        return '', 204
    try:
        data = request.get_json(silent=True) or {}
        ui_language = _request_ui_language(data)
        instruction = str(data.get("instruction", "")).strip()
        if len(instruction) > _cop.MAX_INSTRUCTION_CHARS:
            raise _cop.CopilotError(f"Talimat en fazla {_cop.MAX_INSTRUCTION_CHARS} karakter.")
        if not instruction:
            raise _cop.CopilotError("Talimat boş olamaz.")

        selection = data.get("selection") if isinstance(data.get("selection"), dict) else None
        chat_history = data.get("chat_history") if isinstance(data.get("chat_history"), list) else []
        idempotency_key = data.get("idempotency_key")
        client_version = data.get("document_version")
        use_streaming = request.headers.get("Accept", "").find("text/event-stream") >= 0

        doc = _get_copilot_doc(document_id, request.uid)

        if client_version is not None:
            try:
                parsed_client_version = int(client_version)
            except (TypeError, ValueError) as exc:
                raise _cop.CopilotError("Belge sürümü geçersiz.", 400) from exc
            if parsed_client_version != doc["version"]:
                raise _cop.VersionConflictError()

        # The user may have adjusted a line or page manually since the last AI
        # operation. Accept that state as the next canonical base only when it
        # belongs to the current document version; otherwise a stale tab could
        # overwrite a newer Copilot edit.
        client_layout = data.get("client_layout")
        client_blocks = data.get("client_blocks")
        page_settings_update = None
        if client_layout is not None or client_blocks is not None:
            if not isinstance(client_layout, dict) or not isinstance(client_blocks, list):
                raise _cop.CopilotError("Güncel belge verisi geçersiz.")
            if len(json.dumps(client_layout, ensure_ascii=False)) > 2_000_000:
                raise _cop.CopilotError("Güncel layout çok büyük.", 413)
            if len(client_blocks) > ai_document.MAX_BLOCKS:
                raise _cop.CopilotError(f"En fazla {ai_document.MAX_BLOCKS} blok.")
            sanitized_layout = ai_document.validate_layout(client_layout)
            _capture_manual_line_override_metadata(doc["layout"], sanitized_layout)
            sanitized_blocks = _sanitize_client_copilot_blocks(client_blocks)
            # validate_layout intentionally drops arbitrary settings. Restore
            # the server-owned document settings rather than trusting a client
            # snapshot to keep Copilot state and target-page intent intact.
            sanitized_layout["settings"] = dict(
                (doc.get("layout") or {}).get("settings") or {}
            )
            base_layout, base_blocks = _cop.ensure_document_ids(sanitized_layout, sanitized_blocks)
            base_version = int(client_version) if client_version is not None else int(doc["version"])
            if isinstance(data.get("page_settings"), dict):
                page_settings_update = _canonical_page_settings(
                    data["page_settings"], doc.get("page_settings")
                )
                target = _stored_page_target(page_settings_update)
                if target:
                    base_layout["settings"]["target_page_count"] = target
                else:
                    base_layout["settings"].pop("target_page_count", None)
        else:
            base_version = int(doc["version"])
            base_layout = copy.deepcopy(doc["layout"])
            base_blocks = copy.deepcopy(doc["blocks"])

        if idempotency_key:
            for h in doc["history"]:
                if h.get("idempotency_key") == idempotency_key:
                    page_hashes = {
                        page.get("id", f"p{i}"): _cop.layout_page_hash(page)
                        for i, page in enumerate(doc["layout"].get("pages", []))
                    }
                    return jsonify({
                        "success": True,
                        "cached": True,
                        "version": doc["version"],
                        "assistant_message": h.get("assistant_message") or (
                            "The previous request has already been applied."
                            if ui_language == "en" else "Önceki istek zaten uygulandı."
                        ),
                        "operations": h.get("operations", []),
                        "new_layout": doc["layout"],
                        "new_blocks": doc["blocks"],
                        "page_settings": doc.get("page_settings", {}),
                        "page_hashes": page_hashes,
                        "affected_pages": list(page_hashes.keys()),
                        "reflow_needed": True,
                        "can_undo": bool(doc["history"]),
                        "can_redo": bool(doc["redo_stack"]),
                    })

        # A stored secondary font is sufficient for Copilot to switch a block
        # or line later; the initial document does not have to start in
        # multi-author mode.
        secondary_available = bool(str(doc.get("secondary_font_id") or "").strip())
        configured_model = os.environ.get(_cop.COPILOT_MODEL_ENV, _cop.DEFAULT_COPILOT_MODEL)
        model = ai_document.validate_model(data.get("model") or configured_model)
        provider_config = _ai_provider_config(os.environ.get(
            'COPILOT_PROVIDER_ORDER', DEFAULT_COPILOT_PROVIDER_ORDER
        ))
        req_api_key = provider_config.get("gemini_key", "")

        def _run_edit():
            result = _cop.process_copilot_edit(
                api_key=req_api_key,
                model=model,
                instruction=instruction,
                layout=base_layout,
                blocks=base_blocks,
                selection=selection,
                chat_history=chat_history[-10:],
                secondary_font_available=secondary_available,
                current_version=base_version,
                provider_config=provider_config,
                ui_language=ui_language,
            )
            requested_target, manual_target = ai_document.page_target_intent(instruction)
            result["target_page_count"] = requested_target
            result["page_target_intent"] = (
                "manual" if manual_target else "exact" if requested_target else ""
            )
            return _finalize_copilot_result(
                doc, result, source_layout=base_layout, ui_language=ui_language
            )

        if use_streaming:
            def generate():
                yield _sse_event("status", {"message": (
                    "Interpreting your request…"
                    if ui_language == "en" else "İstek yorumlanıyor…"
                )})
                try:
                    result = _run_edit()
                    if result["needs_clarification"]:
                        yield _sse_event("clarification", {
                            "question": result["clarification_question"],
                            "options": result["clarification_options"],
                            "message": result["assistant_message"],
                            "provider": result.get("provider"),
                            "model": result.get("model"),
                            "fit_report": result.get("fit_report"),
                        })
                        yield _sse_event("complete", {"message": result["assistant_message"]})
                        return

                    yield _sse_event("plan", {
                        "message": result["assistant_message"],
                        "provider": result.get("provider"),
                        "model": result.get("model"),
                        "fit_report": result.get("fit_report"),
                    })
                    yield _sse_event("patch", {"operations": result["operations"]})

                    new_version = result["new_layout"]["version"]
                    record = _cop.make_operation_record(
                        base_version=new_version - 1,
                        new_version=new_version,
                        instruction=instruction,
                        operations=result["operations"],
                        inverse_operations=result["inverse_operations"],
                        user_id=request.uid,
                        idempotency_key=idempotency_key,
                        assistant_message=result["assistant_message"],
                    )
                    persisted_page_settings = _effective_page_settings(
                        result, page_settings_update, doc.get("page_settings")
                    )
                    record["page_settings_before"] = dict(doc.get("page_settings") or {})
                    record["page_settings_after"] = persisted_page_settings
                    try:
                        _store.update_document(
                            document_id, request.uid, base_version, 
                            result["new_layout"], result["new_blocks"], 
                            record, persisted_page_settings
                        )
                    except _store.CopilotStoreError as e:
                        raise _cop.CopilotError(str(e), e.status_code)

                    page_hashes = {
                        page.get("id", f"p{i}"): _cop.layout_page_hash(page)
                        for i, page in enumerate(result["new_layout"].get("pages", []))
                    }
                    affected = list(page_hashes.keys()) if result["reflow_needed"] else []
                    yield _sse_event("layout", {
                        "version": new_version,
                        "affected_pages": affected,
                        "page_hashes": page_hashes,
                        "reflow_needed": result["reflow_needed"],
                        "new_layout": result["new_layout"],
                        "new_blocks": result["new_blocks"],
                        "page_settings": persisted_page_settings,
                        "can_undo": True,
                        "can_redo": False,
                        "provider": result.get("provider"),
                        "model": result.get("model"),
                        "fit_report": result.get("fit_report"),
                    })
                    yield _sse_event("complete", {"message": result["assistant_message"]})

                except _cop.CopilotError as e:
                    yield _sse_event("error", {"message": str(e), "status": e.status_code})
                except Exception as e:
                    logger.error("Copilot stream error: %s", type(e).__name__, exc_info=True)
                    yield _sse_event("error", {"message": (
                        "The Copilot operation could not be completed."
                        if ui_language == "en" else "Copilot işlemi tamamlanamadı."
                    )})

            return Response(
                stream_with_context(generate()),
                mimetype="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                    "Connection": "keep-alive",
                },
            )
        else:
            result = _run_edit()
            if result["needs_clarification"]:
                return jsonify({
                    "success": True,
                    "needs_clarification": True,
                    "clarification_question": result["clarification_question"],
                    "clarification_options": result["clarification_options"],
                    "assistant_message": result["assistant_message"],
                    "provider": result.get("provider"),
                    "model": result.get("model"),
                    "fit_report": result.get("fit_report"),
                    "version": doc["version"],
                })

            new_version = result["new_layout"]["version"]
            record = _cop.make_operation_record(
                base_version=new_version - 1,
                new_version=new_version,
                instruction=instruction,
                operations=result["operations"],
                inverse_operations=result["inverse_operations"],
                user_id=request.uid,
                idempotency_key=idempotency_key,
                assistant_message=result["assistant_message"],
            )
            persisted_page_settings = _effective_page_settings(
                result, page_settings_update, doc.get("page_settings")
            )
            record["page_settings_before"] = dict(doc.get("page_settings") or {})
            record["page_settings_after"] = persisted_page_settings
            try:
                _store.update_document(
                    document_id, request.uid, base_version, 
                    result["new_layout"], result["new_blocks"], 
                    record, persisted_page_settings
                )
            except _store.CopilotStoreError as e:
                raise _cop.CopilotError(str(e), e.status_code)

            page_hashes = {
                page.get("id", f"p{i}"): _cop.layout_page_hash(page)
                for i, page in enumerate(result["new_layout"].get("pages", []))
            }
            return jsonify({
                "success": True,
                "needs_clarification": False,
                "assistant_message": result["assistant_message"],
                "operations": result["operations"],
                "version": new_version,
                "page_hashes": page_hashes,
                "affected_pages": list(page_hashes.keys()) if result["reflow_needed"] else [],
                "reflow_needed": result["reflow_needed"],
                "new_layout": result["new_layout"],
                "new_blocks": result["new_blocks"],
                "page_settings": persisted_page_settings,
                "can_undo": True,
                "can_redo": False,
                "provider": result.get("provider"),
                "model": result.get("model"),
                "fit_report": result.get("fit_report"),
            })

    except Exception as exc:
        return _copilot_error_response(exc)

@app.route("/api/ai/documents/<document_id>/undo", methods=["POST", "OPTIONS"])
@verified_login_required
def copilot_undo(document_id: str):
    if request.method == 'OPTIONS':
        return '', 204
    try:
        ui_language = _request_ui_language()
        doc = _get_copilot_doc(document_id, request.uid)
        if not doc["history"]:
            raise _cop.CopilotError(
                "There is no operation to undo." if ui_language == "en" else "Geri alınacak işlem yok.",
                400,
            )

        record = doc["history"][-1]
        inverse_ops = _remap_line_operation_targets(
            doc["layout"], record.get("inverse_operations", [])
        )
        if not inverse_ops:
            raise _cop.CopilotError(
                "This operation cannot be undone." if ui_language == "en" else "Bu işlem geri alınamıyor.",
                400,
            )

        clean_inv = _cop.validate_and_sanitize_operations(
            inverse_ops, doc["layout"], doc["blocks"],
            secondary_font_available=bool(str(doc.get("secondary_font_id") or "").strip()),
            trusted_internal=True,
        )
        new_layout, new_blocks, redo_inv = _cop.apply_operations(
            clean_inv, doc["layout"], doc["blocks"]
        )
        _sync_line_override_metadata(doc["layout"], new_layout, clean_inv)
        _annotate_line_operation_locators(doc["layout"], redo_inv)
        new_version = doc["version"] + 1
        if _cop.operations_require_reflow(clean_inv):
            new_layout, new_blocks, _ = _copilot_reflow_state(
                doc, new_layout, new_blocks, new_version,
                _stored_page_target(record.get("page_settings_before")),
                clean_inv,
            )
        else:
            new_layout["version"] = new_version
            new_layout, new_blocks = _cop.ensure_document_ids(new_layout, new_blocks)

        redo_record = {
            **record,
            "inverse_operations": redo_inv,
        }
        try:
            _store.undo_document(
                document_id, request.uid, doc["version"], 
                new_layout, new_blocks, redo_record,
                page_settings=record.get("page_settings_before"),
            )
            doc["history"] = doc["history"][:-1]
        except _store.CopilotStoreError as e:
            raise _cop.CopilotError(str(e), e.status_code)

        page_hashes = {
            page.get("id", f"p{i}"): _cop.layout_page_hash(page)
            for i, page in enumerate(new_layout.get("pages", []))
        }
        return jsonify({
            "success": True,
            "version": new_version,
            "new_layout": new_layout,
            "new_blocks": new_blocks,
            "page_settings": record.get("page_settings_before") or {},
            "page_hashes": page_hashes,
            "can_undo": len(doc["history"]) > 0,
            "can_redo": True,
            "message": (
                f"Undid '{record.get('instruction', '...')}'."
                if ui_language == "en" else
                f"'{record.get('instruction', '...')}' geri alındı."
            ),
        })
    except Exception as exc:
        return _copilot_error_response(exc)

@app.route("/api/ai/documents/<document_id>/redo", methods=["POST", "OPTIONS"])
@verified_login_required
def copilot_redo(document_id: str):
    if request.method == 'OPTIONS':
        return '', 204
    try:
        ui_language = _request_ui_language()
        doc = _get_copilot_doc(document_id, request.uid)
        if not doc["redo_stack"]:
            raise _cop.CopilotError(
                "There is no operation to redo." if ui_language == "en" else "İleri alınacak işlem yok.",
                400,
            )

        record = doc["redo_stack"][-1]
        redo_ops = _remap_line_operation_targets(
            doc["layout"], record.get("operations", [])
        )
        if not redo_ops:
            raise _cop.CopilotError(
                "This operation cannot be redone." if ui_language == "en" else "Bu işlem ileri alınamıyor.",
                400,
            )

        clean_ops = _cop.validate_and_sanitize_operations(
            redo_ops, doc["layout"], doc["blocks"],
            secondary_font_available=bool(str(doc.get("secondary_font_id") or "").strip()),
            trusted_internal=True,
        )
        new_layout, new_blocks, inv = _cop.apply_operations(
            clean_ops, doc["layout"], doc["blocks"]
        )
        _sync_line_override_metadata(doc["layout"], new_layout, clean_ops)
        _annotate_line_operation_locators(doc["layout"], inv)
        new_version = doc["version"] + 1
        if _cop.operations_require_reflow(clean_ops):
            new_layout, new_blocks, _ = _copilot_reflow_state(
                doc, new_layout, new_blocks, new_version,
                _stored_page_target(record.get("page_settings_after")),
                clean_ops,
            )
        else:
            new_layout["version"] = new_version
            new_layout, new_blocks = _cop.ensure_document_ids(new_layout, new_blocks)

        hist_record = {**record, "inverse_operations": inv}
        try:
            _store.redo_document(
                document_id, request.uid, doc["version"], 
                new_layout, new_blocks, hist_record,
                page_settings=record.get("page_settings_after"),
            )
            doc["redo_stack"] = doc["redo_stack"][:-1]
        except _store.CopilotStoreError as e:
            raise _cop.CopilotError(str(e), e.status_code)

        page_hashes = {
            page.get("id", f"p{i}"): _cop.layout_page_hash(page)
            for i, page in enumerate(new_layout.get("pages", []))
        }
        return jsonify({
            "success": True,
            "version": new_version,
            "new_layout": new_layout,
            "new_blocks": new_blocks,
            "page_settings": record.get("page_settings_after") or {},
            "page_hashes": page_hashes,
            "can_undo": True,
            "can_redo": len(doc["redo_stack"]) > 0,
            "message": (
                f"Redid '{record.get('instruction', '...')}'."
                if ui_language == "en" else
                f"'{record.get('instruction', '...')}' yeniden uygulandı."
            ),
        })
    except Exception as exc:
        return _copilot_error_response(exc)


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
