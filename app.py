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

# ============================================
# LOGGING SETUP
# ============================================
handlers = [logging.StreamHandler()]
try:
    handlers.append(logging.FileHandler('app.log'))
except (IOError, PermissionError):
    print("Warning: Cannot create log file, using console only")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=handlers
)
logger = logging.getLogger(__name__)

# ============================================
# FLASK APP SETUP
# ============================================
app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max request

# ============================================
# SECURITY: SSRF PROTECTION
# ============================================
ALLOWED_IMAGE_DOMAINS = [
    'cloudinary.com',
    'firebasestorage.googleapis.com',
    'res.cloudinary.com'
]

def is_safe_url(url):
    """URL güvenlik kontrolü - SSRF koruması"""
    try:
        parsed = urlparse(url)
        
        # Sadece HTTPS
        if parsed.scheme != 'https':
            logger.warning(f"Non-HTTPS URL rejected: {url}")
            return False
        
        # Sadece izin verilen domainler
        if not any(parsed.netloc.endswith(domain) for domain in ALLOWED_IMAGE_DOMAINS):
            logger.warning(f"Domain not in whitelist: {parsed.netloc}")
            return False
        
        # Localhost ve private IP'ler yasak
        if 'localhost' in parsed.netloc or '127.0.0.1' in parsed.netloc:
            logger.warning(f"Localhost URL rejected: {url}")
            return False
        
        return True
    except Exception as e:
        logger.error(f"URL validation error: {str(e)}")
        return False

# ============================================
# SECURITY: INPUT VALIDATION
# ============================================
def validate_font_name(name):
    """Font adı validation - XSS ve path traversal koruması"""
    if not name or not isinstance(name, str):
        raise ValueError("Font name required")
    
    name = name.strip()
    
    if len(name) < 3 or len(name) > 50:
        raise ValueError("Font name must be 3-50 characters")
    
    # XSS koruması
    if re.search(r'[<>]', name):
        raise ValueError("Font name contains invalid characters")
    
    # Path traversal koruması
    if '..' in name or '/' in name or '\\' in name:
        raise ValueError("Font name contains invalid characters")
    
    return name

def validate_base64_image(b64_string, max_size_mb=10):
    """Base64 image validation"""
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
        if img.format not in ['JPEG', 'PNG']:
            raise ValueError(f"Unsupported image format: {img.format}")
        
        # Dimension kontrolü
        width, height = img.size
        if width > 4000 or height > 4000:
            raise ValueError(f"Image too large: {width}x{height} (max 4000x4000)")
        
        return img_data
        
    except ValueError as e:
        raise e
    except Exception as e:
        raise ValueError(f"Invalid image data: {str(e)}")

# ============================================
# CORS CONFIGURATION (DÜZELTILMIŞ!)
# ============================================
CORS(app, resources={
    r"/api/*": {
        "origins": [
            "https://fontify.online",
            "https://www.fontify.online",
            "https://elyazisi-api.onrender.com",
            "https://square-morning-7a87.ismailacarmain.workers.dev",
        ],
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"]
    },
    r"/process_single": {
        "origins": [
            "https://fontify.online",
            "https://www.fontify.online"
        ],
        "methods": ["POST", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"]
    },
    r"/download": {
        "origins": [
            "https://fontify.online",
            "https://www.fontify.online"
        ],
        "methods": ["GET", "OPTIONS"]
    }
})

# ============================================
# RECAPTCHA
# ============================================
RECAPTCHA_SECRET_KEY = os.environ.get('RECAPTCHA_SECRET_KEY')

def verify_recaptcha(token):
    """reCAPTCHA v3 doğrulama"""
    if not RECAPTCHA_SECRET_KEY:
        logger.warning("RECAPTCHA_SECRET_KEY not configured")
        return False
    
    if not token:
        logger.warning("No reCAPTCHA token provided")
        return False
    
    try:
        response = requests.post(
            'https://www.google.com/recaptcha/api/siteverify',
            data={
                'secret': RECAPTCHA_SECRET_KEY,
                'response': token
            },
            timeout=5
        )
        result = response.json()
        
        success = result.get('success', False)
        score = result.get('score', 0)
        
        if not success:
            logger.warning(f"reCAPTCHA verification failed: {result}")
            return False
        
        if score < 0.5:
            logger.warning(f"reCAPTCHA score too low: {score}")
            return False
        
        logger.info(f"reCAPTCHA verified successfully: score={score}")
        return True
        
    except Exception as e:
        logger.error(f"reCAPTCHA verification error: {str(e)}")
        return False

# ============================================
# FIREBASE INITIALIZATION
# ============================================
db = None
init_error = None

def init_firebase():
    """Firebase initialization - secure"""
    global db, init_error
    
    try:
        cred = None
        connected_project_id = "Unknown"
        
        # Environment variable'dan al (Production)
        env_creds = os.environ.get('FIREBASE_CREDENTIALS')
        if env_creds:
            cred_dict = json.loads(env_creds)
            cred = credentials.Certificate(cred_dict)
            connected_project_id = cred_dict.get('project_id', 'Environment')
        
        # Yerel dosya kontrolü (Development only)
        if not cred:
            paths = ['serviceAccountKey.json', '/etc/secrets/serviceAccountKey.json']
            for p in paths:
                if os.path.exists(p):
                    cred = credentials.Certificate(p)
                    with open(p, 'r') as f:
                        connected_project_id = json.load(f).get('project_id', 'File')
                    break
        
        if cred:
            if not firebase_admin._apps:
                firebase_admin.initialize_app(cred)
            db = firestore.client()
            logger.info(f"✅ Firebase connected: {connected_project_id}")
        else:
            logger.error("❌ Firebase credentials not found")
            
    except Exception as e:
        init_error = str(e)
        db = None
        logger.error(f"❌ Firebase initialization error: {e}")
    
    return db

# Initialize Firebase on startup
init_firebase()

# ============================================
# HTTPS ENFORCEMENT
# ============================================
@app.before_request
def before_request():
    """HTTPS zorunluluğu (production)"""
    if not request.is_secure and not request.headers.get('X-Forwarded-Proto') == 'https':
        if not app.debug and not request.host.startswith('localhost'):
            return redirect(request.url.replace('http://', 'https://'), code=301)

# ============================================
# SECURITY HEADERS
# ============================================
@app.after_request
def set_secure_headers(response):
    """Security headers"""
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

# ============================================
# ERROR HANDLERS
# ============================================
@app.errorhandler(Exception)
def handle_exception(e):
    """Global exception handler"""
    logger.error(f"Unhandled exception: {str(e)}", exc_info=True)
    return jsonify({
        "success": False,
        "message": "Sunucu hatası oluştu",
        "error": str(e) if app.debug else None
    }), 500

@app.errorhandler(413)
def request_entity_too_large(error):
    """File too large handler"""
    return jsonify({
        "success": False,
        "message": "Dosya çok büyük (max 50MB)"
    }), 413

# ============================================
# AUTH MIDDLEWARE (DÜZELTİLMİŞ!)
# ============================================
def login_required(f):
    """Firebase auth token verification"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Get Authorization header
        auth_header = request.headers.get('Authorization')
        
        if not auth_header:
            logger.warning(f"Missing Authorization header from {request.remote_addr}")
            return jsonify({
                'success': False,
                'message': 'Giriş yapmanız gerekiyor',
                'error': 'MISSING_AUTH'
            }), 401
        
        # Parse Bearer token
        try:
            id_token = auth_header.split('Bearer ')[-1]
        except:
            logger.warning(f"Invalid Authorization header format from {request.remote_addr}")
            return jsonify({
                'success': False,
                'message': 'Geçersiz token formatı',
                'error': 'INVALID_TOKEN_FORMAT'
            }), 401
        
        # Verify token
        try:
            decoded_token = auth.verify_id_token(id_token)
            request.uid = decoded_token['uid']
            request.user_email = decoded_token.get('email', 'unknown')
            logger.info(f"User authenticated: {request.uid}")
            
        except auth.ExpiredIdTokenError:
            logger.warning(f"Expired token from {request.remote_addr}")
            return jsonify({
                'success': False,
                'message': 'Oturumunuz sona erdi. Lütfen tekrar giriş yapın.',
                'error': 'TOKEN_EXPIRED'
            }), 401
            
        except auth.RevokedIdTokenError:
            logger.warning(f"Revoked token from {request.remote_addr}")
            return jsonify({
                'success': False,
                'message': 'Token iptal edilmiş.',
                'error': 'TOKEN_REVOKED'
            }), 401
            
        except Exception as e:
            logger.error(f"Token verification failed: {str(e)}")
            return jsonify({
                'success': False,
                'message': 'Token doğrulanamadı',
                'error': 'TOKEN_VERIFICATION_FAILED'
            }), 401
        
        return f(*args, **kwargs)
    
    return decorated_function

# ============================================
# CREDIT SYSTEM (DÜZELTİLMİŞ!)
# ============================================
def check_credits(required=1):
    """Credit kontrolü decorator - login_required'dan SONRA çalışmalı!"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # request.uid burada mevcut (login_required'dan geliyor)
            try:
                if not db:
                    logger.error("Firestore not initialized")
                    return jsonify({
                        'success': False,
                        'message': 'Veritabanı bağlantısı yok'
                    }), 500
                
                user_doc = db.collection('users').document(request.uid).get()
                
                if not user_doc.exists:
                    # Yeni kullanıcı - varsayılan credit ver
                    db.collection('users').document(request.uid).set({
                        'credits': 10,
                        'email': request.user_email,
                        'created_at': firestore.SERVER_TIMESTAMP
                    })
                    user_credits = 10
                    logger.info(f"New user created: {request.uid} with 10 credits")
                else:
                    user_data = user_doc.to_dict()
                    user_credits = user_data.get('credits', 0)
                
                logger.info(f"User {request.uid} has {user_credits} credits (required: {required})")
                
                # Credit yetersiz
                if user_credits < required:
                    logger.warning(f"User {request.uid} insufficient credits: {user_credits} < {required}")
                    return jsonify({
                        'success': False,
                        'message': f'Kredi yetersiz. Mevcut: {user_credits}, Gerekli: {required}',
                        'error': 'INSUFFICIENT_CREDITS',
                        'current_credits': user_credits,
                        'required_credits': required
                    }), 403  # 403 Forbidden (401 değil!)
                
                # Credit azalt
                db.collection('users').document(request.uid).update({
                    'credits': firestore.Increment(-required),
                    'last_used': firestore.SERVER_TIMESTAMP
                })
                
                logger.info(f"User {request.uid} used {required} credit(s), {user_credits - required} remaining")
                
                # Request'e credit bilgisini ekle
                request.user_credits_before = user_credits
                request.user_credits_after = user_credits - required
                
            except Exception as e:
                logger.error(f"Credit check failed: {str(e)}", exc_info=True)
                return jsonify({
                    'success': False,
                    'message': 'Kredi kontrolü başarısız',
                    'error': 'CREDIT_CHECK_FAILED'
                }), 500
            
            return f(*args, **kwargs)
        
        return decorated_function
    return decorator

# ============================================
# RATE LIMITING (Simple)
# ============================================
last_upload_times = {}
RATE_LIMIT_SECONDS = 10  # 10 saniyede bir upload

def check_rate_limit():
    """Basit rate limiting"""
    if not hasattr(request, 'uid'):
        return True
    
    current_time = time.time()
    last_time = last_upload_times.get(request.uid, 0)
    
    if current_time - last_time < RATE_LIMIT_SECONDS:
        remaining = int(RATE_LIMIT_SECONDS - (current_time - last_time))
        logger.warning(f"Rate limit hit for user {request.uid}")
        return False, remaining
    
    last_upload_times[request.uid] = current_time
    return True, 0

# ============================================
# ROUTES: HEALTH & BASIC
# ============================================
@app.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "firebase": "connected" if db else "disconnected"
    }), 200

@app.route('/')
def index():
    """Ana sayfa"""
    return render_template('index.html')

@app.route('/upload')
def upload_page():
    """Upload sayfası"""
    return render_template('upload.html')

# ============================================
# ROUTES: USER ENDPOINTS
# ============================================
@app.route('/api/user/credits', methods=['GET'])
@login_required
def get_user_credits():
    """Kullanıcının credit bilgisini döndür"""
    try:
        if not db:
            return jsonify({'success': False, 'message': 'Veritabanı bağlantısı yok'}), 500
        
        user_doc = db.collection('users').document(request.uid).get()
        
        if not user_doc.exists:
            # Yeni kullanıcı oluştur
            credits = 10
            db.collection('users').document(request.uid).set({
                'credits': credits,
                'email': request.user_email,
                'created_at': firestore.SERVER_TIMESTAMP
            })
            logger.info(f"New user created in get_credits: {request.uid}")
        else:
            user_data = user_doc.to_dict()
            credits = user_data.get('credits', 0)
        
        return jsonify({
            'success': True,
            'credits': credits
        }), 200
        
    except Exception as e:
        logger.error(f"Get credits error: {str(e)}")
        return jsonify({
            'success': False,
            'message': 'Kredi bilgisi alınamadı'
        }), 500

@app.route('/api/user/fonts', methods=['GET'])
@login_required
def get_user_fonts():
    """Kullanıcının fontlarını listele"""
    try:
        if not db:
            return jsonify({'success': False, 'message': 'Veritabanı bağlantısı yok'}), 500
        
        # Kullanıcının fontları
        fonts_ref = db.collection('fonts').where('owner_id', '==', request.uid).stream()
        
        fonts = []
        for doc in fonts_ref:
            font_data = doc.to_dict()
            font_data['id'] = doc.id
            fonts.append(font_data)
        
        logger.info(f"User {request.uid} has {len(fonts)} fonts")
        
        return jsonify({
            'success': True,
            'fonts': fonts
        }), 200
        
    except Exception as e:
        logger.error(f"Get fonts error: {str(e)}")
        return jsonify({
            'success': False,
            'message': 'Font listesi alınamadı'
        }), 500

# ============================================
# ROUTES: FONT UPLOAD (DÜZELTİLMİŞ SIRALAMA!)
# ============================================
@app.route('/api/upload_form', methods=['POST'])
@login_required  # 1. Önce auth kontrol (request.uid set edilir)
@check_credits(required=1)  # 2. Sonra credit kontrol (request.uid kullanır)
def upload_form():
    """
    Font yükleme endpoint
    ÖNEMLİ: Decorator sıralaması kritik!
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({
                'success': False,
                'message': 'Veri eksik'
            }), 400
        
        # Font name validation
        try:
            font_name = validate_font_name(data.get('font_name', ''))
        except ValueError as e:
            return jsonify({
                'success': False,
                'message': str(e)
            }), 400
        
        # Image data validation
        image_data = data.get('image')
        if not image_data:
            return jsonify({
                'success': False,
                'message': 'Resim verisi eksik'
            }), 400
        
        # Rate limiting
        allowed, remaining = check_rate_limit()
        if not allowed:
            return jsonify({
                'success': False,
                'message': f'Çok hızlı istek. {remaining} saniye bekleyin.'
            }), 429
        
        # Base64 image validation
        try:
            img_bytes = validate_base64_image(image_data, max_size_mb=10)
        except ValueError as e:
            return jsonify({
                'success': False,
                'message': str(e)
            }), 400
        
        # reCAPTCHA validation (opsiyonel)
        recaptcha_token = data.get('recaptchaToken')
        if recaptcha_token:
            if not verify_recaptcha(recaptcha_token):
                logger.warning(f"reCAPTCHA failed for user {request.uid}")
                # İsteğe bağlı: return error veya sadece log
        
        # Image processing (core_generator modülünüzle)
        logger.info(f"Processing font: {font_name} for user {request.uid}")
        
        # TODO: Gerçek font işleme kodunuz buraya gelecek
        # result = core_generator.process_font(img_bytes, font_name)
        
        # Firestore'a kaydet
        if db:
            font_doc = {
                'name': font_name,
                'owner_id': request.uid,
                'owner_email': request.user_email,
                'is_public': False,
                'created_at': firestore.SERVER_TIMESTAMP,
                # 'characters': result['characters'],
                # 'download_url': result['url']
            }
            
            doc_ref = db.collection('fonts').add(font_doc)
            logger.info(f"Font created: {doc_ref[1].id}")
        
        return jsonify({
            'success': True,
            'message': 'Font başarıyla oluşturuldu',
            'remaining_credits': request.user_credits_after
        }), 200
        
    except Exception as e:
        logger.error(f"Upload form error: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'message': 'Font oluşturma başarısız',
            'error': str(e) if app.debug else 'INTERNAL_ERROR'
        }), 500

# ============================================
# ROUTES: FONT OPERATIONS
# ============================================
@app.route('/api/toggle_visibility', methods=['POST'])
@login_required
def toggle_visibility():
    """Font görünürlüğünü değiştir (public/private)"""
    try:
        if not db:
            return jsonify({'success': False, 'message': 'Veritabanı bağlantısı yok'}), 500
        
        data = request.get_json()
        font_id = data.get('font_id')
        
        if not font_id:
            return jsonify({'success': False, 'message': 'Font ID eksik'}), 400
        
        # Font'u kontrol et
        font_ref = db.collection('fonts').document(font_id)
        font_doc = font_ref.get()
        
        if not font_doc.exists:
            return jsonify({'success': False, 'message': 'Font bulunamadı'}), 404
        
        font_data = font_doc.to_dict()
        
        # Sadece owner değiştirebilir
        if font_data.get('owner_id') != request.uid:
            return jsonify({'success': False, 'message': 'Yetkiniz yok'}), 403
        
        # Toggle
        current_visibility = font_data.get('is_public', False)
        new_visibility = not current_visibility
        
        font_ref.update({'is_public': new_visibility})
        logger.info(f"Font {font_id} visibility changed to {new_visibility}")
        
        return jsonify({
            'success': True,
            'is_public': new_visibility
        }), 200
        
    except Exception as e:
        logger.error(f"Toggle visibility error: {str(e)}")
        return jsonify({'success': False, 'message': 'İşlem başarısız'}), 500

@app.route('/api/update_char', methods=['POST'])
@login_required
def update_char():
    """Font karakterini güncelle"""
    try:
        if not db:
            return jsonify({'success': False, 'message': 'Veritabanı bağlantısı yok'}), 500
        
        data = request.get_json()
        font_id = data.get('font_id')
        char = data.get('char')
        image_data = data.get('image')
        
        if not all([font_id, char, image_data]):
            return jsonify({'success': False, 'message': 'Eksik veri'}), 400
        
        # Font ownership kontrolü
        font_ref = db.collection('fonts').document(font_id)
        font_doc = font_ref.get()
        
        if not font_doc.exists:
            return jsonify({'success': False, 'message': 'Font bulunamadı'}), 404
        
        font_data = font_doc.to_dict()
        
        if font_data.get('owner_id') != request.uid:
            return jsonify({'success': False, 'message': 'Yetkiniz yok'}), 403
        
        # Karakter güncelle
        # TODO: Gerçek güncelleme kodunuz
        logger.info(f"Character {char} updated in font {font_id}")
        
        return jsonify({'success': True}), 200
        
    except Exception as e:
        logger.error(f"Update char error: {str(e)}")
        return jsonify({'success': False, 'message': 'Güncelleme başarısız'}), 500

# ============================================
# ROUTES: DOWNLOAD
# ============================================
@app.route('/download', methods=['GET'])
def download():
    """Download endpoint - URL'den dosya indir"""
    try:
        url = request.args.get('url')
        
        if not url:
            return jsonify({'success': False, 'message': 'URL eksik'}), 400
        
        # SSRF koruması
        if not is_safe_url(url):
            logger.warning(f"Unsafe URL rejected in download: {url}")
            return jsonify({'success': False, 'message': 'Güvenli olmayan URL'}), 400
        
        # Dosyayı indir
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        
        # Content type belirle
        content_type = response.headers.get('Content-Type', 'application/octet-stream')
        
        # Dosya adını URL'den çıkar
        filename = url.split('/')[-1].split('?')[0] or 'download'
        
        return send_file(
            io.BytesIO(response.content),
            mimetype=content_type,
            as_attachment=True,
            download_name=filename
        )
        
    except requests.RequestException as e:
        logger.error(f"Download error: {str(e)}")
        return jsonify({'success': False, 'message': 'İndirme başarısız'}), 500

# ============================================
# ROUTES: MOBILE (No auth required)
# ============================================
@app.route('/process_single', methods=['POST'])
def process_single():
    """
    Mobil endpoint - Auth gerekmez
    NOT: Credit kontrolü YOK (dikkat!)
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({'success': False, 'message': 'Veri eksik'}), 400
        
        # reCAPTCHA kontrolü
        recaptcha_token = data.get('recaptchaToken')
        if not verify_recaptcha(recaptcha_token):
            return jsonify({'success': False, 'message': 'Bot koruması başarısız'}), 403
        
        # Image processing
        image_data = data.get('image')
        if not image_data:
            return jsonify({'success': False, 'message': 'Resim eksik'}), 400
        
        try:
            img_bytes = validate_base64_image(image_data, max_size_mb=5)
        except ValueError as e:
            return jsonify({'success': False, 'message': str(e)}), 400
        
        # TODO: Font processing
        logger.info("Processing single image (mobile)")
        
        return jsonify({
            'success': True,
            'message': 'İşlem başarılı'
        }), 200
        
    except Exception as e:
        logger.error(f"Process single error: {str(e)}")
        return jsonify({'success': False, 'message': 'İşlem başarısız'}), 500

# ============================================
# RUN SERVER
# ============================================
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    
    # Development vs Production
    is_production = os.environ.get('FLASK_ENV') == 'production'
    
    if is_production:
        logger.info(f"🚀 Starting PRODUCTION server on port {port}")
        app.run(host='0.0.0.0', port=port, debug=False)
    else:
        logger.info(f"🔧 Starting DEVELOPMENT server on port {port}")
        app.run(host='0.0.0.0', port=port, debug=True)
