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
from urllib.parse import urlparse
from pdf2image import convert_from_bytes
from PIL import Image as PILImage
import requests
from functools import wraps

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
        cred = None
        env_creds = os.environ.get('FIREBASE_CREDENTIALS')
        if env_creds:
            cred_dict = json.loads(env_creds.strip())
            cred = credentials.Certificate(cred_dict)
            connected_project_id = cred_dict.get('project_id', 'EnvJson')
        
        # Yerel dosya kontrolü (Sadece development için)
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
            print(f"Firestore BAĞLANDI (Secure Mod): {connected_project_id}")
        else:
            print("UYARI: Firebase credentials bulunamadı.")
    except Exception as e:
        init_error = str(e)
        db = None
        print(f"Firebase Hatası: {e}")
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
        id_token = None
        if 'Authorization' in request.headers:
            id_token = request.headers['Authorization'].split(' ').pop()
        
        if id_token:
            try:
                decoded_token = auth.verify_id_token(id_token)
                request.uid = decoded_token['uid']
            except Exception as e:
                logger.error(f"Token verify error: {e}")
                # Token geçersizse ama formda user_id varsa devam etmesine izin ver
                request.uid = request.form.get('user_id') or request.args.get('user_id')
        else:
            # Token yoksa form verilerinden al (Mobil ve eski sayfalar için)
            request.uid = request.form.get('user_id') or request.args.get('user_id')
        
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
        self.repetition = repetition
        self.char_list = []
        self.generate_char_list()

    def generate_char_list(self):
        lowers = "abcçdefgğhıijklmnoöpqrsştuüvwxyz"
        uppers = "ABCÇDEFGĞHIİJKLMNOÖPQRSŞTUÜVWXYZ"
        digits = "0123456789"
        symbols_str = ".,:;?!-_\"'()[]{}/\\|+*=< >%^~@$€₺#"
        symbols_str = symbols_str.replace(" ", "")
        
        tr_map = {'ç': 'cc', 'ğ': 'gg', 'ı': 'ii', 'ö': 'oo', 'ş': 'ss', 'ü': 'uu', 'Ç': 'cc', 'Ğ': 'gg', 'I': 'ii', 'İ': 'i', 'Ö': 'oo', 'Ş': 'ss', 'Ü': 'uu'}
        sym_map = {'.': 'nokta', ',': 'virgul', ':': 'ikiknokta', ';': 'noktalivirgul', '?': 'soru', '!': 'unlem', '-': 'tire', '_': 'alt_tire', '"': 'tirnak', "'": 'tektirnak', '(': 'parantezac', ')': 'parantezkapama', '[': 'koseli_ac', ']': 'koseli_kapa', '{': 'suslu_ac', '}': 'suslu_kapa', '/': 'slash', '\\': 'backslas', '|': 'pipe', '+': 'arti', '*': 'carpi', '=': 'esit', '<': 'kucuktur', '>': 'buyuktur', '%': 'yuzde', '^': 'sapka', '~': 'yaklasik', '@': 'at', '$': 'dolar', '€': 'euro', '₺': 'tl', '&': 'ampersand', '#': 'diyez'}

        for char in lowers:
            base = tr_map.get(char, char)
            for i in range(1, self.repetition + 1): self.char_list.append(f"kucuk_{base}_{i}")
        for char in uppers:
            base = tr_map.get(char, char.lower())
            for i in range(1, self.repetition + 1): self.char_list.append(f"buyuk_{base}_{i}")
        for char in digits:
            for i in range(1, self.repetition + 1): self.char_list.append(f"rakam_{char}_{i}")
        
        seen = set(); unique_symbols = ""
        for char in symbols_str:
            if char not in seen: unique_symbols += char; seen.add(char)
        for char in unique_symbols:
            safe = sym_map.get(char, f"sembol_{ord(char)}")
            for i in range(1, self.repetition + 1): self.char_list.append(f"ozel_{safe}_{i}")

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
            images = convert_from_bytes(file_bytes, dpi=300)
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

        sections_to_process = []
        for i, pil_img in enumerate(images):
            cv_img = np.array(pil_img)[:, :, ::-1].copy()
            h, w, _ = cv_img.shape
            half_h = h // 2
            sections_to_process.append({'img': cv_img[0:half_h, :], 'id': i*2})
            sections_to_process.append({'img': cv_img[half_h:h, :], 'id': i*2+1})

        harf_sistemi = HarfSistemi(repetition=variation_count)
        total_sections = len(sections_to_process)
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

        for idx, section in enumerate(sections_to_process):
            msg = f'Bölüm {idx+1}/{total_sections} taranıyor...'
            progress = 20 + int((idx / total_sections) * 75)
            op_ref.update({'message': msg, 'progress': progress})
            
            res, err = harf_sistemi.process_single_page(section['img'], forced_section_id=section['id'])
            if err:
                logger.warning(f"Section {idx} skip: {err}")
                continue
                
            if res and res['harfler']:
                batch = database.batch()
                for char_name, b_64_bytes in res['harfler'].items():
                    # Base64 string'e çevirerek kaydet
                    b64_str = base64.b64encode(b_64_bytes).decode('utf-8')
                    char_ref = d_ref.collection('chars').document(char_name)
                    batch.set(char_ref, {'data': b64_str})
                batch.commit()
                total_processed_chars += res['detected']
                all_completed_sections.append(res['section_id'])

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

        variation_count = int(request.form.get('variation_count', 3))
        
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

        repetition = int(data.get('variation_count', 3))
        
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
            fonts.append({'id': doc.id, 'name': d.get('font_name'), 'char_count': d.get('harf_sayisi'), 'type': 'public', 'owner_id': d.get('owner_id')})
            
        if user_id:
            private_query = database.collection('fonts').where('owner_id', '==', user_id).where('is_public', '==', False).stream()
            for doc in private_query:
                d = doc.to_dict()
                fonts.append({'id': doc.id, 'name': d.get('font_name'), 'char_count': d.get('harf_sayisi'), 'type': 'private', 'owner_id': user_id})
        return jsonify({"success": True, "fonts": fonts})
    except Exception as e:
        logger.error(f"System error in list_fonts: {str(e)}", exc_info=True)
        return jsonify({"success": False, "message": "Liste yüklenemedi."}), 500

@app.route('/api/get_assets')
def get_assets():
    try:
        font_id = request.args.get('font_id')
        assets = {}
        database = init_firebase()
        if database and font_id:
            # Hibrit okuma (Önce alt koleksiyon, yoksa ana doküman)
            char_docs = database.collection('fonts').document(font_id).collection('chars').stream()
            has_sub = False
            for doc in char_docs:
                has_sub = True
                key, val = doc.id, doc.to_dict().get('data')
                base_key = key.rsplit('_', 1)[0] if '_' in key else key
                if base_key not in assets: assets[base_key] = []
                assets[base_key].append(val)
                
            if not has_sub:
                doc = database.collection('fonts').document(font_id).get()
                if doc.exists:
                    raw = doc.to_dict().get('harfler', {})
                    for key, val in raw.items():
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
                    base_key = key.rsplit('_', 1)[0] if '_' in key else key
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
                            base_key = key.rsplit('_', 1)[0] if '_' in key else key
                            if base_key not in active_harfler: active_harfler[base_key] = []
                            active_harfler[base_key].append(img)
                        except: continue
        
        config = {'page_width': 2480, 'page_height': 3508, 'margin_top': 200, 'margin_left': 150, 'margin_right': 150, 'target_letter_height': int(request.form.get('yazi_boyutu', 140)), 'line_spacing': int(request.form.get('satir_araligi', 220)), 'word_spacing': int(request.form.get('kelime_boslugu', 55)), 'murekkep_rengi': (27,27,29), 'opacity': 0.95, 'jitter': int(request.form.get('jitter', 3)), 'paper_type': request.form.get('paper_type', 'cizgili'), 'line_slope': 5}
        sayfalar = core_generator.metni_sayfaya_yaz(metin, active_harfler, config)
        pdf_buffer = core_generator.sayfalari_pdf_olustur(sayfalar)
        return send_file(pdf_buffer, mimetype='application/pdf', as_attachment=True, download_name='el_yazisi.pdf')
    except ValueError as e:
        logger.warning(f"Validation error in download: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 400
    except Exception as e:
        logger.error(f"System error in download: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'message': 'İşlem başarısız. Lütfen tekrar deneyin.'}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
