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
    """URL gÃ¼venlik kontrolÃ¼"""
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
    """Font adÄ±nÄ± doÄŸrula"""
    if not name or not isinstance(name, str):
        raise ValueError("Font name required")
    
    name = name.strip()
    
    if len(name) < 3 or len(name) > 50:
        raise ValueError("Font name must be 3-50 characters")
    
    # XSS ve path traversal korumasÄ±
    if re.search(r'[<>]', name):
        raise ValueError("Font name contains invalid characters")
    
    if '..' in name or '/' in name or '\\' in name:
        raise ValueError("Font name contains invalid characters")
    
    return name

def validate_base64_image(b64_string, max_size_mb=5):
    """Base64 image doÄŸrula"""
    try:
        if not b64_string or not isinstance(b64_string, str):
            raise ValueError("Invalid image data")
        
        # Data URL prefix'ini kaldÄ±r
        if ',' in b64_string:
            b64_string = b64_string.split(',')[1]
        
        # Decode
        img_data = base64.b64decode(b64_string, validate=True)
        
        # Boyut kontrolÃ¼
        size_mb = len(img_data) / (1024 * 1024)
        if size_mb > max_size_mb:
            raise ValueError(f"Image too large: {size_mb:.1f}MB (max {max_size_mb}MB)")
        
        # Format kontrolÃ¼
        img = PILImage.open(io.BytesIO(img_data))
        if img.format not in ['JPEG', 'PNG', 'JPG']:
            raise ValueError(f"Invalid format: {img.format}")
        
        # Dimension kontrolÃ¼
        if img.width > 4000 or img.height > 4000:
            raise ValueError("Image dimensions too large")
        
        return b64_string
    except Exception as e:
        raise ValueError(f"Invalid image: {str(e)}")

app = Flask(__name__, template_folder='templates', static_folder='static')

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
    'Authorization', 'Content-Type', 'X-Gemini-Api-Key'
], methods=['GET', 'POST', 'OPTIONS'])

@app.route('/health')
def health():
    return jsonify({"status": "healthy"}), 200

@app.route('/forms/<path:filename>')
@app.route('/pdfler/<path:filename>')
def serve_forms(filename):
    # _ORNEK veya ORNEK taleplerini _DOLU olarak yÃ¶nlendir (frontend uyumu iÃ§in)
    if "ORNEK" in filename:
        # EÄŸer dosya adÄ±nda 1x, 3x gibi ibareler varsa onlarÄ± koru
        for v in ["1", "2", "3", "5", "10"]:
            if f"{v}x" in filename:
                return send_file(os.path.join('static/forms', f"form_{v}x_DOLU.pdf"))
    
    # Normal servis
    try:
        return send_file(os.path.join('static/forms', filename))
    except:
        # Fallback: EÄŸer dosya bulunamazsa ama bir varyasyon isteniyorsa varsayÄ±lanÄ± ver
        return send_file(os.path.join('static/forms', 'form_3x_BOS.pdf'))

# --- FIREBASE BAÄLANTISI ---
db = None
connected_project_id = "BILINMIYOR"
init_error = None

# 2. GÃœVENLÄ°K: Secret Key Env Var (Koddan Silindi)
RECAPTCHA_SECRET_KEY = os.environ.get('RECAPTCHA_SECRET_KEY')

def verify_recaptcha(token):
    if not RECAPTCHA_SECRET_KEY:
        allow_insecure = app.debug or os.environ.get('ALLOW_INSECURE_RECAPTCHA', '').lower() == 'true'
        if allow_insecure:
            logger.warning("reCAPTCHA secret is missing; insecure development bypass is active.")
            return True
        logger.error("RECAPTCHA_SECRET_KEY is required in production.")
        return False
    
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
        cred = None
        env_creds = os.environ.get('FIREBASE_CREDENTIALS')
        
        if env_creds:
            env_creds = env_creds.strip()
            logger.info(f"FIREBASE_CREDENTIALS bulundu. Uzunluk: {len(env_creds)} karakter.")
            
            # JSON formatÄ±nÄ± zorla dÃ¼zeltmeye Ã§alÄ±ÅŸ (Render kopyalama hatalarÄ± iÃ§in)
            try:
                cred_dict = json.loads(env_creds)
                cred = credentials.Certificate(cred_dict)
                connected_project_id = cred_dict.get('project_id', 'EnvJson')
            except json.JSONDecodeError as je:
                logger.error(f"!!! KRÄ°TÄ°K: JSON FormatÄ± HatalÄ± !!! Hata: {je}")
                # EÄŸer JSON tÄ±rnak hatasÄ± varsa basit bir tamir dene
                try:
                    import ast
                    cred_dict = ast.literal_eval(env_creds)
                    cred = credentials.Certificate(cred_dict)
                    connected_project_id = cred_dict.get('project_id', 'AstFixed')
                    logger.info("JSON hatasÄ± ast.literal_eval ile tamir edildi.")
                except:
                    logger.error("JSON tamir edilemedi. LÃ¼tfen Render'daki iÃ§eriÄŸi kontrol edin.")
        else:
            logger.error("!!! HATA: FIREBASE_CREDENTIALS bulunamadÄ±. Render panelini kontrol edin !!!")
        
        if not cred:
            # Yedek plan: Gizli dosya olarak eklenmiÅŸ olabilir mi?
            paths = ['serviceAccountKey.json', '/etc/secrets/serviceAccountKey.json', 'firebase_key.json']
            for p in paths:
                if os.path.exists(p):
                    logger.info(f"Firebase anahtarÄ± dosyada bulundu: {p}")
                    cred = credentials.Certificate(p)
                    with open(p, 'r') as f:
                        data = json.load(f)
                        connected_project_id = data.get('project_id', 'File')
                    break
                    
        if not cred and os.environ.get('FIREBASE_PROJECT_ID') and os.environ.get('FIREBASE_PRIVATE_KEY'):
            logger.info("AyrÄ± ayrÄ± FIREBASE_* environment deÄŸiÅŸkenleri bulundu. Credential oluÅŸturuluyor...")
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
            logger.info(f"âœ… FIREBASE BAÅARIYLA BAÄLANDI | Proje: {connected_project_id}")
        else:
            logger.error("âŒ Firebase baÅŸlatÄ±lamadÄ±: GeÃ§erli bir anahtar yok.")
            
    except Exception as e:
        init_error = str(e)
        db = None
        logger.error(f"ğŸ”¥ Firebase HatasÄ±: {str(e)}", exc_info=True)
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
        "message": "Sunucu tarafÄ±nda bir hata oluÅŸtu.",
        "error": str(e) if app.debug else None
    }), 500

@app.before_request
def before_request():
    """HTTPS zorunluluÄŸu (production)"""
    if not request.is_secure and not request.headers.get('X-Forwarded-Proto') == 'https':
        if not app.debug and not request.host.startswith('localhost'):
            from flask import redirect
            return redirect(request.url.replace('http://', 'https://'), code=301)

@app.after_request
def set_secure_headers(response):
    """GÃ¼venlik header'larÄ± ekle"""
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
        authorization = request.headers.get('Authorization', '').strip()
        parts = authorization.split()
        if len(parts) != 2 or parts[0].lower() != 'bearer' or not parts[1]:
            return jsonify({'success': False, 'message': 'GÃ¼venli oturum gerekli.'}), 401
        try:
            decoded = auth.verify_id_token(parts[1])
            request.uid = decoded['uid']
            request.auth_verified = True
        except Exception as exc:
            logger.warning('Firebase token doÄŸrulanamadÄ±: %s', type(exc).__name__)
            return jsonify({'success': False, 'message': 'Oturum doÄŸrulanamadÄ±. LÃ¼tfen yeniden giriÅŸ yapÄ±n.'}), 401
        return f(*args, **kwargs)
    return decorated_function


def optional_verified_uid():
    """Return the verified Firebase UID, or None when no token was supplied."""
    authorization = request.headers.get('Authorization', '').strip()
    if not authorization:
        return None
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != 'bearer' or not parts[1]:
        raise ValueError('GeÃ§ersiz Authorization baÅŸlÄ±ÄŸÄ±.')
    try:
        return auth.verify_id_token(parts[1])['uid']
    except Exception as exc:
        logger.warning('Opsiyonel Firebase token doÄŸrulanamadÄ±: %s', type(exc).__name__)
        raise ValueError('Oturum doÄŸrulanamadÄ±.') from exc

# --- KREDÄ° SÄ°STEMÄ° ---
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
    
    # VeritabanÄ± henÃ¼z baÄŸlanmamÄ±ÅŸsa veya user_id yoksa bile 10 gÃ¶ster (UI kÄ±rÄ±lmasÄ±n)
    if not db:
        logger.warning("Firestore DB not initialized yet, returning default 10.")
        return jsonify({'credits': 10})
        
    if not user_id:
        return jsonify({'credits': 0})
        
    try:
        doc = db.collection('users').document(user_id).get()
        if doc.exists:
            # KullanÄ±cÄ± varsa kredisini getir, yoksa 10 say.
            user_data = doc.to_dict()
            credits = user_data.get('credits', 10)
            return jsonify({'credits': credits})
        else:
            # KullanÄ±cÄ± veritabanÄ±nda hiÃ§ yoksa (ilk defa giriyorsa) 10 kredisi vardÄ±r.
            return jsonify({'credits': 10})
    except Exception as e:
        logger.error(f"Kredi okuma hatasÄ±: {e}")
        return jsonify({'credits': 10})

# --- HARF TARAMA MOTORU (AynÄ± KalÄ±yor) ---
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
            # DPI'yi 200'e Ã§ekerek Render'Ä±n 512MB RAM limitine takÄ±lmayÄ± Ã¶nlÃ¼yoruz
            images = convert_from_bytes(file_bytes, dpi=200)
            logger.info(f"PDF converted to {len(images)} images")
        except Exception as pdf_err:
            logger.warning(f"PDF conversion failed: {pdf_err}. Trying as raw image.")
            try:
                img = PILImage.open(io.BytesIO(file_bytes)).convert('RGB')
                images = [img]
            except Exception as img_err:
                raise ValueError(f"Dosya okunamadÄ±: {str(img_err)}")

        if not images:
            raise ValueError("Ä°ÅŸlenecek sayfa bulunamadÄ±.")

        harf_sistemi = HarfSistemi(repetition=variation_count)
        total_sections = len(images) * 2
        total_processed_chars = 0
        all_completed_sections = []

        op_ref.update({'message': f'Toplam {total_sections} bÃ¶lÃ¼m iÅŸlenecek...', 'progress': 20})

        d_ref = database.collection('fonts').document(font_id)
        u_ref = database.collection('users').document(user_id).collection('fonts').document(font_id)
        
        # Font dokÃ¼manÄ±nÄ± hazÄ±rla
        if not d_ref.get().exists:
            init_payload = {
                'font_name': font_name, 'font_id': font_id, 'owner_id': user_id, 'user_id': user_id,
                'repetition': variation_count, 'created_at': firestore.SERVER_TIMESTAMP,
                'harf_sayisi': 0, 'sections_completed': [], 'is_public': False
            }
            d_ref.set(init_payload)
            u_ref.set(init_payload)

        # BelleÄŸi ÅŸiÅŸirmemek iÃ§in her sayfayÄ± tek tek iÅŸle
        section_idx = 0
        for i, pil_img in enumerate(images):
            cv_img = np.array(pil_img)[:, :, ::-1]
            h, w, _ = cv_img.shape
            half_h = h // 2
            
            # --- ÃœST BÃ–LÃœM (Section 1) ---
            msg = f'BÃ¶lÃ¼m {section_idx+1}/{total_sections} taranÄ±yor...'
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
            
            # --- ALT BÃ–LÃœM (Section 2) ---
            msg = f'BÃ¶lÃ¼m {section_idx+1}/{total_sections} taranÄ±yor...'
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
            
            # Ä°ÅŸlenmiÅŸ sayfayÄ± bellekten at
            images[i] = None

        # Final gÃ¼ncelleme
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
            'message': f'TamamlandÄ±! {total_processed_chars} karakter eklendi.', 
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

# Dosya GÃ¼venlik AyarlarÄ±
ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg'}
MAX_FILE_SIZE = 10 * 1024 * 1024 # 10 MB

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/api/upload_form', methods=['POST'])
@verified_login_required
def upload_form():
    try:
        user_id = request.uid
        if request.content_length and request.content_length > MAX_FILE_SIZE + (512 * 1024):
            return jsonify({'success': False, 'message': 'Dosya en fazla 10 MB olabilir.'}), 413
        
        # 1. Bot korumasÄ±
        if not verify_recaptcha(request.form.get('recaptcha_token')):
            logger.warning(f"reCAPTCHA validation failed - User: {user_id}")
            return jsonify({'success': False, 'message': 'GÃ¼venlik doÄŸrulamasÄ± baÅŸarÄ±sÄ±z.'}), 403

        # 2. Dosya KontrolÃ¼
        uploaded_files = request.files.getlist('file') or request.files.getlist('files')
        
        if not uploaded_files or not uploaded_files[0].filename:
            return jsonify({'success': False, 'message': 'Dosya yÃ¼klenmedi.'}), 400
            
        file = uploaded_files[0]
        
        if not allowed_file(file.filename):
            return jsonify({'success': False, 'message': 'GeÃ§ersiz dosya tÃ¼rÃ¼.'}), 400

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
                return jsonify({'success': False, 'message': 'Dosya geÃ§erli bir PDF deÄŸil.'}), 400
        else:
            try:
                with PILImage.open(io.BytesIO(file_bytes)) as probe:
                    probe.verify()
                    if (probe.format or '').upper() not in {'PNG', 'JPEG', 'JPG'}:
                        raise ValueError('unsupported image')
            except Exception:
                return jsonify({'success': False, 'message': 'Dosya geÃ§erli bir PNG/JPEG deÄŸil.'}), 400

        database = init_firebase()
        if database is None:
            return jsonify({'success': False, 'message': 'VeritabanÄ± ÅŸu anda kullanÄ±lamÄ±yor.'}), 503

        # Kredi ancak tÃ¼m doÄŸrulamalar baÅŸarÄ±yla geÃ§tikten sonra dÃ¼ÅŸÃ¼lÃ¼r.
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
        return jsonify({'success': False, 'message': 'Ä°ÅŸlem baÅŸarÄ±sÄ±z. LÃ¼tfen tekrar deneyin.'}), 500

@app.route('/api/mobile_upload_session', methods=['POST'])
@verified_login_required
def create_mobile_upload_session():
    try:
        data = request.get_json(silent=True) or {}
        font_name = validate_font_name(data.get('font_name'))
        repetition = validate_variation_count(data.get('variation_count', 3))
        database = init_firebase()
        if database is None:
            return jsonify({'success': False, 'message': 'VeritabanÄ± kullanÄ±lamÄ±yor.'}), 503
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
        return jsonify({'success': False, 'message': 'Mobil yÃ¼kleme oturumu oluÅŸturulamadÄ±.'}), 500


@app.route('/process_single', methods=['POST'])
def process_single():
    try:
        data = request.get_json(silent=True) or {}
        if not verify_recaptcha(data.get('recaptcha_token')): return jsonify({'success': False, 'message': 'GÃ¼venlik doÄŸrulamasÄ± baÅŸarÄ±sÄ±z.'}), 403

        try:
            database = init_firebase()
            if database is None:
                return jsonify({'success': False, 'message': 'VeritabanÄ± kullanÄ±lamÄ±yor.'}), 503
            session_id = str(data.get('session_id', '')).strip()
            session_ref = None
            session_data = None
            if re.fullmatch(r'[0-9a-f]{32}', session_id):
                session_ref = database.collection('mobile_upload_sessions').document(session_id)
                session_snapshot = session_ref.get()
                if not session_snapshot.exists:
                    return jsonify({'success': False, 'message': 'Mobil yÃ¼kleme oturumu bulunamadÄ±.'}), 401
                session_data = session_snapshot.to_dict() or {}
                if int(session_data.get('expires_at', 0)) < int(time.time()):
                    return jsonify({'success': False, 'message': 'Mobil yÃ¼kleme oturumunun sÃ¼resi doldu.'}), 401
                u_id = session_data.get('owner_id')
                f_name = validate_font_name(session_data.get('font_name'))
                repetition = validate_variation_count(session_data.get('variation_count'))
            elif app.debug or os.environ.get('ALLOW_LEGACY_MOBILE_UPLOADS', '').lower() == 'true':
                u_id = data.get('user_id')
                f_name = validate_font_name(data.get('font_name'))
                repetition = validate_variation_count(data.get('variation_count', 3))
            else:
                return jsonify({'success': False, 'message': 'GÃ¼venli mobil yÃ¼kleme oturumu gerekli.'}), 401
            b64 = validate_base64_image(data.get('image_base64'))
        except ValueError as e:
            return jsonify({'success': False, 'message': str(e)}), 400

        if session_ref is not None:
            allowed, msg = charge_mobile_session_once(session_ref, u_id)
            if not allowed:
                return jsonify({'success': False, 'message': 'Yetersiz kredi veya geÃ§ersiz mobil oturum.'}), 402
        elif session_ref is None:
            allowed, msg = check_and_deduct_credit(u_id)
            if not allowed:
                return jsonify({'success': False, 'message': 'Yetersiz kredi.'}), 402

        h_sistemi = HarfSistemi(repetition=repetition)
        nparr = np.frombuffer(base64.b64decode(b64), np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None: return jsonify({'success': False, 'message': 'Resim hatasÄ±'}), 400

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
        return jsonify({'success': False, 'message': 'Ä°ÅŸlem baÅŸarÄ±sÄ±z. LÃ¼tfen tekrar deneyin.'}), 500

@app.route('/api/toggle_visibility', methods=['POST'])
@verified_login_required
def toggle_visibility():
    try:
        data = request.get_json()
        font_id = data.get('font_id')
        user_id = request.uid # Token'dan gelen gÃ¼venli ID
        
        database = init_firebase()
        font_ref = database.collection('fonts').document(font_id)
        doc = font_ref.get()
        
        if not doc.exists: return jsonify({'success': False, 'message': 'Font bulunamadÄ±'}), 404
        if doc.to_dict().get('owner_id') != user_id: return jsonify({'success': False, 'message': 'Yetkisiz iÅŸlem'}), 403
            
        new_status = not doc.to_dict().get('is_public', False)
        font_ref.update({'is_public': new_status})
        return jsonify({'success': True, 'new_status': new_status})
    except ValueError as e:
        logger.warning(f"Validation error in toggle_visibility: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 400
    except Exception as e:
        logger.error(f"System error in toggle_visibility: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'message': 'Ä°ÅŸlem baÅŸarÄ±sÄ±z.'}), 500


@app.route('/api/delete_font', methods=['POST'])
@verified_login_required
def delete_font():
    """Remove a library entry, or delete the full font when the requester owns it."""
    try:
        data = request.get_json(silent=True) or {}
        font_id = str(data.get('font_id', '')).strip()
        if len(font_id) < 3 or len(font_id) > 180 or '/' in font_id or '..' in font_id or re.search(r'[\x00-\x1f]', font_id):
            return jsonify({'success': False, 'message': 'GeÃ§ersiz font kimliÄŸi.'}), 400
        database = init_firebase()
        if database is None:
            return jsonify({'success': False, 'message': 'VeritabanÄ± kullanÄ±lamÄ±yor.'}), 503
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
        raise DigitalUploadAPIError('client_upload_id biÃ§imi geÃ§ersiz.', 400)
    return value


def _validate_server_font_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'digital_[0-9a-f]{32}', value):
        raise DigitalUploadAPIError('font_id geÃ§ersiz.', 400)
    return value


def _load_digital_font(database, font_id, owner_id):
    font_id = _validate_server_font_id(font_id)
    font_ref = database.collection('fonts').document(font_id)
    snapshot = font_ref.get()
    if not snapshot.exists:
        raise DigitalUploadAPIError('Dijital font yÃ¼klemesi bulunamadÄ±.', 404)
    font_data = snapshot.to_dict() or {}
    if font_data.get('owner_id') != owner_id:
        raise DigitalUploadAPIError('Bu font Ã¼zerinde iÅŸlem yetkiniz yok.', 403)
    if font_data.get('source') != 'digital':
        raise DigitalUploadAPIError('Bu font dijital yÃ¼kleme protokolÃ¼ne ait deÄŸil.', 409)
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
                    'Bu client_upload_id farklÄ± bir yÃ¼kleme iÃ§in daha Ã¶nce kullanÄ±lmÄ±ÅŸ.',
                    409,
                )
            existing_font_id = session_data.get('font_id')
            existing_ref = database.collection('fonts').document(existing_font_id)
            existing_snapshot = existing_ref.get(transaction=txn)
            if not existing_snapshot.exists:
                raise DigitalUploadAPIError(
                    'YÃ¼kleme oturumu mevcut ancak font kaydÄ± bulunamÄ±yor.', 409
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
        raise DigitalUploadAPIError('chars, en az bir harf iÃ§eren nesne olmalÄ±dÄ±r.', 400)
    if len(chars) > MAX_DRAWN_FONT_CHARS_PER_APPEND:
        raise DigitalUploadAPIError(
            f'Bir append isteÄŸinde en fazla {MAX_DRAWN_FONT_CHARS_PER_APPEND} harf gÃ¶nderilebilir.',
            413,
        )

    font_ref, font_data = _load_digital_font(database, font_id, owner_id)
    if font_data.get('status') != 'draft':
        raise DigitalUploadAPIError('YalnÄ±zca draft durumundaki fontlara harf eklenebilir.', 409)

    repetition = validate_variation_count(font_data.get('repetition'))
    expected_keys = variation_key_set(repetition)
    supplied_keys = set(chars)
    invalid_keys = sorted(supplied_keys - expected_keys)
    if invalid_keys:
        raise DigitalUploadAPIError(
            'Ä°zin verilmeyen harf anahtarÄ± gÃ¶nderildi.',
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
        raise DigitalUploadAPIError('Font finalize edilebilir durumda deÄŸil.', 409)

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
            'Font henÃ¼z tamamlanmadÄ±; beklenen harf seti eksik veya geÃ§ersiz.',
            409,
            **details,
        )

    transaction = database.transaction()

    @firestore.transactional
    def finish(txn):
        latest_snapshot = font_ref.get(transaction=txn)
        if not latest_snapshot.exists:
            raise DigitalUploadAPIError('Dijital font yÃ¼klemesi bulunamadÄ±.', 404)
        latest = latest_snapshot.to_dict() or {}
        if latest.get('owner_id') != owner_id:
            raise DigitalUploadAPIError('Bu font Ã¼zerinde iÅŸlem yetkiniz yok.', 403)
        if latest.get('status') == 'ready':
            return True, None
        if latest.get('status') != 'draft':
            raise DigitalUploadAPIError('Font finalize edilebilir durumda deÄŸil.', 409)

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
        raise DigitalUploadAPIError('chars, en az bir harf iÃ§eren nesne olmalÄ±dÄ±r.', 400)
    if len(chars) > len(variation_key_set(repetition)):
        raise DigitalUploadAPIError('Beklenenden fazla harf gÃ¶nderildi.', 400)

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
                'message': 'Dijital font yÃ¼klemek iÃ§in doÄŸrulanmÄ±ÅŸ oturum gereklidir.',
            }), 401

        if (
            request.content_length is not None
            and request.content_length > MAX_DRAWN_FONT_REQUEST_BYTES
        ):
            return jsonify({
                'success': False,
                'message': 'Ä°stek gÃ¶vdesi Ã§ok bÃ¼yÃ¼k (en fazla 20 MB).',
            }), 413
        raw_body = request.get_data(cache=True)
        if len(raw_body) > MAX_DRAWN_FONT_REQUEST_BYTES:
            return jsonify({
                'success': False,
                'message': 'Ä°stek gÃ¶vdesi Ã§ok bÃ¼yÃ¼k (en fazla 20 MB).',
            }), 413

        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise DigitalUploadAPIError('GeÃ§erli bir JSON nesnesi gÃ¶nderilmelidir.', 400)

        database = init_firebase()
        if database is None:
            return jsonify({
                'success': False,
                'message': 'VeritabanÄ± ÅŸu anda kullanÄ±lamÄ±yor. LÃ¼tfen tekrar deneyin.',
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
                'mode; start, append veya finalize olmalÄ±dÄ±r.', 400
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
            'message': 'Dijital font yÃ¼kleme servisi ÅŸu anda kullanÄ±lamÄ±yor.',
        }), 503


@app.route('/api/update_char', methods=['POST'])
@verified_login_required
def update_char():
    try:
        data = request.get_json()
        font_id, char_key, image_base64 = data.get('font_id'), data.get('char_key'), data.get('image_base64')
        user_id = request.uid # Token'dan gelen gÃ¼venli ID
        
        database = init_firebase()
        font_ref = database.collection('fonts').document(font_id)
        font_doc = font_ref.get()
        
        if not font_doc.exists: return jsonify({'success': False, 'message': 'Font bulunamadÄ±'}), 404
        if font_doc.to_dict().get('owner_id') != user_id: return jsonify({'success': False, 'message': 'Yetkisiz iÅŸlem!'}), 403
            
        try:
            image_base64 = validate_base64_image(image_base64)
        except ValueError as e:
            return jsonify({'success': False, 'message': str(e)}), 400

        font_ref.collection('chars').document(char_key).set({'data': image_base64})
        return jsonify({'success': True, 'message': 'Harf gÃ¼ncellendi'})
    except ValueError as e:
        logger.warning(f"Validation error in update_char: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 400
    except Exception as e:
        logger.error(f"System error in update_char: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'message': 'GÃ¼ncelleme baÅŸarÄ±sÄ±z.'}), 500

@app.route('/api/list_fonts')
def list_fonts():
    database = init_firebase()
    if not database:
        return jsonify({"success": False, "message": "VeritabanÄ± kullanÄ±lamÄ±yor."}), 503
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
        return jsonify({"success": False, "message": "Liste yÃ¼klenemedi."}), 500

@app.route('/api/add_to_library', methods=['POST'])
@verified_login_required
def add_to_library():
    try:
        data = request.get_json()
        user_id = request.uid
        font_id = data.get('font_id')
        if not font_id: return jsonify({'success':False}), 400
        
        orig_ref = db.collection('fonts').document(font_id).get()
        if not orig_ref.exists: return jsonify({'success':False, 'message': 'Font bulunamadÄ±'}), 404
        
        orig_data = orig_ref.to_dict()
        if orig_data.get('status') not in (None, 'ready'):
            return jsonify({'success': False, 'message': 'Taslak font kÃ¼tÃ¼phaneye eklenemez.'}), 409
        if not orig_data.get('is_public', False) and orig_data.get('owner_id') != user_id:
            return jsonify({'success': False, 'message': 'Bu Ã¶zel fontu kopyalama yetkiniz yok.'}), 403
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
                    return jsonify({'success': False, 'message': 'Font henÃ¼z tamamlanmadÄ±.'}), 409
                if not font_data.get('is_public', False):
                    try:
                        requester = optional_verified_uid()
                    except ValueError as exc:
                        return jsonify({'success': False, 'message': str(exc)}), 401
                    if requester != font_data.get('owner_id'):
                        return jsonify({'success': False, 'message': 'Bu Ã¶zel fonta eriÅŸim yetkiniz yok.'}), 403
            # Hibrit okuma (Ã¶nce alt koleksiyon, yoksa ana dokÃ¼man)
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
        return jsonify({"success": False, "message": "Assets yÃ¼klenemedi."}), 500

@app.route('/download', methods=['POST'])
def download():
    try:
        font_id, metin = request.form.get('font_id'), request.form.get('metin', '')
        active_harfler = {}
        database = init_firebase()
        if database and font_id:
            font_snapshot = database.collection('fonts').document(font_id).get()
            if not font_snapshot.exists:
                return jsonify({'success': False, 'message': 'Font bulunamadÄ±.'}), 404
            font_data = font_snapshot.to_dict() or {}
            if not font_data.get('is_public', False):
                id_token = request.form.get('id_token', '')
                try:
                    requester = auth.verify_id_token(id_token).get('uid')
                except Exception:
                    return jsonify({'success': False, 'message': 'Ã–zel font iÃ§in gÃ¼venli oturum gerekli.'}), 401
                if requester != font_data.get('owner_id'):
                    return jsonify({'success': False, 'message': 'Bu Ã¶zel fonta eriÅŸim yetkiniz yok.'}), 403
            # get_assets mantÄ±ÄŸÄ±yla aynÄ±sÄ±nÄ± yap (Hibrit)
            char_docs = database.collection('fonts').document(font_id).collection('chars').stream()
            has_sub = False
            for doc in char_docs:
                has_sub = True
                key, b64 = doc.id, doc.to_dict().get('data')
                try:
                    img = core_generator.Image.open(io.BytesIO(_raw_character_bytes(b64))).convert("RGBA")
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
        sayfalar = list(core_generator.metni_sayfaya_yaz(metin, active_harfler, config))
        
        # Overlay Ekle (Serbest Ã‡izim)
        overlay_b64 = request.form.get('overlay_b64')
        if overlay_b64 and sayfalar:
            try:
                overlay_data = overlay_b64.split(",")[1] if "," in overlay_b64 else overlay_b64
                overlay_img = core_generator.Image.open(io.BytesIO(base64.b64decode(overlay_data))).convert("RGBA")
                # overlay_img boyutunu sayfa boyutuyla aynÄ± yap
                if overlay_img.size != sayfalar[0].size:
                    overlay_img = overlay_img.resize(sayfalar[0].size, core_generator.Image.Resampling.LANCZOS)
                # Sadece ilk sayfaya (veya o anki ekrana) yapÄ±ÅŸtÄ±rÄ±yoruz
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
        return jsonify({'success': False, 'message': 'Ä°ÅŸlem baÅŸarÄ±sÄ±z. LÃ¼tfen tekrar deneyin.'}), 500

# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# AI DOCUMENT STUDIO
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _font_access_for_user(font_id, user_id, *, require_ready=True):
    if not isinstance(font_id, str) or not font_id.strip() or len(font_id) > 180:
        raise ai_document.AiDocumentError('GeÃ§erli bir font_id gerekli.')
    database = init_firebase()
    if database is None:
        raise ai_document.AiDocumentError('VeritabanÄ± ÅŸu anda kullanÄ±lamÄ±yor.', 503)
    font_ref = database.collection('fonts').document(font_id.strip())
    snapshot = font_ref.get()
    if not snapshot.exists:
        raise ai_document.AiDocumentError('Font bulunamadÄ±.', 404)
    font_data = snapshot.to_dict() or {}
    is_public = bool(font_data.get('is_public', False))
    if not is_public and font_data.get('owner_id') != user_id:
        raise ai_document.AiDocumentError('Bu font iÃ§in eriÅŸim yetkiniz yok.', 403)
    if require_ready and font_data.get('source') == 'digital' and font_data.get('status') != 'ready':
        raise ai_document.AiDocumentError('Bu font henÃ¼z tamamlanmamÄ±ÅŸ bir taslak.', 409)
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
        raise ValueError('Karakter verisi okunamadÄ±.') from exc


def _gemini_api_key():
    """Prefer a per-request BYOK key, then the Render-only server secret."""
    return ai_document.choose_api_key(
        request.headers.get('X-Gemini-Api-Key'),
        os.environ.get('GEMINI_API_KEY'),
    )


def _load_glyph_value(raw):
    if isinstance(raw, str) and raw.lower().startswith('https://'):
        if not is_safe_url(raw):
            raise ValueError('GÃ¼venli olmayan karakter URL adresi.')
        response = requests.get(raw, timeout=8, stream=True)
        response.raise_for_status()
        content_length = int(response.headers.get('Content-Length', '0') or 0)
        if content_length > 2 * 1024 * 1024:
            raise ValueError('Karakter gÃ¶rseli Ã§ok bÃ¼yÃ¼k.')
        chunks = []
        downloaded = 0
        for chunk in response.iter_content(64 * 1024):
            downloaded += len(chunk)
            if downloaded > 2 * 1024 * 1024:
                raise ValueError('Karakter gÃ¶rseli Ã§ok bÃ¼yÃ¼k.')
            chunks.append(chunk)
        raw_bytes = b''.join(chunks)
    else:
        raw_bytes = _raw_character_bytes(raw)
    if len(raw_bytes) > 2 * 1024 * 1024:
        raise ValueError('Karakter gÃ¶rseli Ã§ok bÃ¼yÃ¼k.')
    with PILImage.open(io.BytesIO(raw_bytes)) as image:
        image.load()
        if image.width > 2048 or image.height > 2048:
            raise ValueError('Karakter gÃ¶rseli boyut sÄ±nÄ±rÄ±nÄ± aÅŸÄ±yor.')
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
        raise ai_document.AiDocumentError('Font karakter sÄ±nÄ±rÄ±nÄ± aÅŸÄ±yor.', 413)
    documents.sort(key=lambda item: item.id)
    for char_doc in documents:
        raw = (char_doc.to_dict() or {}).get('data')
        if raw is None:
            continue
        try:
            _append_font_glyph(grouped, char_doc.id, raw)
        except Exception as exc:
            logger.warning('Font karakteri atlandÄ± (%s): %s', char_doc.id, type(exc).__name__)

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
                        logger.warning('Eski font karakteri atlandÄ± (%s): %s', storage_key, type(exc).__name__)
    if not grouped:
        raise ai_document.AiDocumentError('Font karakterleri yÃ¼klenemedi.', 422)
    return grouped


def _ai_error_response(exc):
    if isinstance(exc, ai_document.AiDocumentError):
        return jsonify({'success': False, 'message': str(exc)}), exc.status_code
    logger.error('AI Document Studio error: %s', type(exc).__name__, exc_info=True)
    return jsonify({'success': False, 'message': 'Belge iÅŸlemi ÅŸu anda tamamlanamadÄ±.'}), 500


@app.route('/api/ai/status', methods=['GET'])
@verified_login_required
def ai_status():
    return jsonify({
        'success': True,
        'server_key_configured': bool(os.environ.get('GEMINI_API_KEY', '').strip()),
        'default_model': 'gemini-3.1-pro-preview',
    })


@app.route('/api/ai/test', methods=['POST'])
@verified_login_required
def ai_connection_test():
    try:
        data = request.get_json(silent=True) or {}
        model = data.get('model', 'gemini-3.1-pro-preview')
        ai_document.test_gemini_connection(_gemini_api_key(), model)
        return jsonify({'success': True, 'model': ai_document.validate_model(model)})
    except Exception as exc:
        return _ai_error_response(exc)


@app.route('/api/ai/plan', methods=['POST'])
@verified_login_required
def ai_document_plan():
    try:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ai_document.AiDocumentError('GeÃ§erli bir JSON gÃ¶vdesi gerekli.')
        font_ref, font_data = _font_access_for_user(data.get('font_id', ''), request.uid)
        harfler = _load_font_images(font_ref)
        source = str(data.get('source', 'ai'))
        if source == 'manual':
            blocks = ai_document.manual_blocks(
                ai_document.normalize_text(data.get('text_content', '')),
                ai_document.normalize_text(data.get('title', ''), maximum=180),
            )
            layout = ai_document.build_layout(blocks, harfler, data.get('page_settings'))
            result = {
                'layout': layout,
                'blocks': blocks,
                'full_text': '\n'.join(block['text'] for block in blocks),
                'summary': 'Metin gerÃ§ek font Ã¶lÃ§Ã¼leriyle mizanpajlandÄ±.',
                'font_profile': ai_document.font_profile(
                    harfler,
                    font_data.get('repetition', 1),
                    layout['settings']['letter_scale'],
                ),
                'model': None,
            }
        elif source == 'ai':
            result = ai_document.create_ai_layout(
                api_key=_gemini_api_key(),
                model=data.get('model', 'gemini-3.1-pro-preview'),
                template=str(data.get('template', 'odev')),
                topic=data.get('topic', ''),
                instructions=data.get('instructions', ''),
                harfler=harfler,
                repetition=font_data.get('repetition', 1),
                page_settings=data.get('page_settings'),
            )
        else:
            raise ai_document.AiDocumentError("source yalnÄ±zca 'ai' veya 'manual' olabilir.")
        return jsonify({'success': True, 'font_name': font_data.get('font_name'), **result})
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
            raise ai_document.AiDocumentError('letter_scale 50-260 arasÄ±nda olmalÄ±.')
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


def _send_layout_pdf(layout, harfler, filename='fontify_belge.pdf'):
    clean_layout = ai_document.validate_layout(layout)
    pages = core_generator.metni_koordinatli_yaz(clean_layout, harfler)
    pdf_buffer = core_generator.sayfalari_pdf_olustur(pages)
    if pdf_buffer is None:
        raise ai_document.AiDocumentError('PDF sayfasÄ± oluÅŸturulamadÄ±.', 422)
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


@app.route('/api/ai_layout_pdf', methods=['POST'])
@verified_login_required
def ai_layout_pdf():
    try:
        if request.content_length and request.content_length > 2 * 1024 * 1024:
            raise ai_document.AiDocumentError('Layout isteÄŸi en fazla 2 MB olabilir.', 413)
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ai_document.AiDocumentError('GeÃ§erli bir JSON gÃ¶vdesi gerekli.')
        font_ref, _ = _font_access_for_user(data.get('font_id', ''), request.uid)
        return _send_layout_pdf(data.get('layout'), _load_font_images(font_ref), 'fontify_ai_belge.pdf')
    except Exception as exc:
        return _ai_error_response(exc)


@app.route('/api/ai_generate_pdf', methods=['POST'])
@verified_login_required
def ai_generate_pdf():
    """Backward-compatible manual text renderer using the safe layout engine."""
    try:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ai_document.AiDocumentError('GeÃ§erli bir JSON gÃ¶vdesi gerekli.')
        font_ref, _ = _font_access_for_user(data.get('font_id', ''), request.uid)
        harfler = _load_font_images(font_ref)
        blocks = ai_document.manual_blocks(ai_document.normalize_text(data.get('text_content', '')))
        layout = ai_document.build_layout(blocks, harfler, data.get('page_settings'))
        overrides = data.get('per_line_overrides') if isinstance(data.get('per_line_overrides'), dict) else {}
        flat_lines = [line for page in layout['pages'] for line in page['lines']]
        for key, values in overrides.items():
            try:
                target = flat_lines[int(key)]
            except (ValueError, IndexError, TypeError):
                continue
            if not isinstance(values, dict):
                continue
            for field in ('letter_scale', 'letter_spacing', 'word_spacing', 'line_slope', 'jitter', 'ink_color', 'line_offset_y'):
                if field in values:
                    target[field] = values[field]
        return _send_layout_pdf(layout, harfler)
    except Exception as exc:
        return _ai_error_response(exc)


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)


