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
            except Exception as e: init_error = f"Env Vars Hatası: {e}"

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
            print("UYARI: Firebase credentials bulunamadı.")
    except Exception as e:
        init_error = str(e)
        db = None
        print(f"Firebase Hatası: {e}")
    return db

init_firebase()

# --- HARF TARAMA MOTORU (GELİŞMİŞ ARUCO SİSTEMİ) ---
class HarfSistemi:
    def __init__(self, repetition=3):
        self.repetition = repetition
        self.char_list = []
        self.generate_char_list()

    def generate_char_list(self):
        # Web uyumlu ASCII isimlendirme
        lowers = "abcçdefgğhıijklmnoöpqrsştuüvwxyz"
        uppers = "ABCÇDEFGĞHIİJKLMNOÖPQRSŞTUÜVWXYZ"
        digits = "0123456789"
        symbols_str = ".,:;?!-_\"'()[]{}/\\|+*=< >%^~@$€₺&#"
        symbols_str = symbols_str.replace(" ", "")
        
        # Türkçe ve Özel Karakter Haritası (engine.js ile %100 uyumlu olmalı)
        # Önemli: I -> buyuk_ii, İ -> buyuk_i, ı -> kucuk_ii, i -> kucuk_i
        tr_map = {
            'ç': 'cc', 'ğ': 'gg', 'ı': 'ii', 'ö': 'oo', 'ş': 'ss', 'ü': 'uu',
            'Ç': 'cc', 'Ğ': 'gg', 'I': 'ii', 'İ': 'i', 'Ö': 'oo', 'Ş': 'ss', 'Ü': 'uu'
        }
        
        # Sembol haritası
        sym_map = {
            ".": "nokta", ",": "virgul", ":": "ikiknokta", ";": "noktalivirgul", 
            "?": "soru", "!": "unlem", "-": "tire", "_": "alt_tire",
            "\"": "tirnak", "'": "tektirnak", 
            "(": "parantezac", ")": "parantezkapama",
            "[": "koseli_ac", "]": "koseli_kapa",
            "{": "suslu_ac", "}": "suslu_kapa",
            "/": "slash", "\\": "backslas", "|": "pipe",
            "+": "arti", "*": "carpi", "=": "esit",
            "<": "kucuktur", ">": "buyuktur",
            "%": "yuzde", "^": "sapka", "~": "yaklasik",
            "@": "at", "$": "dolar", "€": "euro", "₺": "tl",
            "&": "ampersand", "#": "diyez"
        }

        # Küçük harfler
        for char in lowers:
            base = tr_map.get(char, char)
            for i in range(1, self.repetition + 1):
                self.char_list.append(f"kucuk_{base}_{i}")
        
        # Büyük harfler
        for char in uppers:
            # tr_map içinde varsa onu kullan (I->ii gibi), yoksa lowercase yap
            if char in tr_map:
                base = tr_map[char]
            else:
                base = char.lower()
            
            for i in range(1, self.repetition + 1):
                self.char_list.append(f"buyuk_{base}_{i}")
        
        # Rakamlar
        for char in digits:
            for i in range(1, self.repetition + 1):
                self.char_list.append(f"rakam_{char}_{i}")
        
        # Semboller
        seen = set()
        unique_symbols = ""
        for char in symbols_str:
            if char not in seen:
                unique_symbols += char
                seen.add(char)

        for char in unique_symbols:
            safe = sym_map.get(char, f"sembol_{ord(char)}")
            for i in range(1, self.repetition + 1):
                self.char_list.append(f"ozel_{safe}_{i}")

    def crop_tight(self, binary_img):
        coords = cv2.findNonZero(binary_img)
        if coords is None: return None
        x, y, w, h = cv2.boundingRect(coords)
        # Çok küçük gürültüleri ele (Noktalama işaretleri için limiti düşürdüm: 2px)
        if w < 2 or h < 2: return None
        return binary_img[y:y+h, x:x+w]

    def process_roi(self, roi):
        if roi.size == 0: return None
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        
        # Hafif bir yumuşatma
        gray = cv2.GaussianBlur(gray, (3,3), 0)
        
        # Adaptive Threshold
        thresh = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 21, 10)
        
        tight = self.crop_tight(thresh)
        if tight is None: return None
        
        h, w = tight.shape
        rgba = np.zeros((h, w, 4), dtype=np.uint8)
        rgba[:, :, 0] = 0
        rgba[:, :, 1] = 0
        rgba[:, :, 2] = 0
        rgba[:, :, 3] = tight
        
        return rgba

    def process_single_page(self, img, forced_section_id=None):
        # ... (Marker tespit kodları aynı kalıyor) ...
        # Marker tespiti için grayscale yap
        gray_full = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        
        aruco_dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
        parameters = cv2.aruco.DetectorParameters()
        parameters.adaptiveThreshWinSizeMin = 3
        parameters.adaptiveThreshWinSizeMax = 23
        parameters.adaptiveThreshWinSizeStep = 5
        
        detector = cv2.aruco.ArucoDetector(aruco_dict, parameters)
        
        corners, ids, _ = detector.detectMarkers(gray_full)
        
        # Eğer bulunamazsa kontrast artırıp tekrar dene
        if ids is None or len(ids) < 4:
            clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8,8))
            enhanced = clahe.apply(gray_full)
            corners, ids, _ = detector.detectMarkers(enhanced)
        
        if ids is None or len(ids) < 4:
            return None, f"Yetersiz marker ({0 if ids is None else len(ids)}/4). Lütfen fotoğrafı dik ve net çekin."
        
        ids = ids.flatten()
        
        # 2. Bölüm Tespiti
        if forced_section_id is not None:
            bid = forced_section_id
            start_id = bid * 4
            expected = [(start_id + k) % 50 for k in range(4)]
        else:
            base = int(min(ids))
            bid = base // 4
            start_id = bid * 4
            expected = [start_id, start_id+1, start_id+2, start_id+3]
        
        # 3. Perspektif
        src_points = []
        found_centers = {}
        for idx in range(len(ids)):
            found_centers[ids[idx]] = np.mean(corners[idx][0], axis=0)
            
        missing = []
        for target in expected:
            if target in found_centers: src_points.append(found_centers[target])
            else: missing.append(target)
                
        if missing: return None, f"Bölüm {bid} için markerlar eksik: {missing}"
            
        src = np.float32(src_points)
        scale = 10; sw, sh = 210 * scale, 148 * scale; m = 175
        dst = np.float32([[m, m], [sw-m, m], [m, sh-m], [sw-m, sh-m]])
        M = cv2.getPerspectiveTransform(src, dst)
        warped = cv2.warpPerspective(img, M, (sw, sh))
        
        # 4. Izgara Kesimi
        b_px = 150
        sx = int((sw - 10*b_px)/2)
        sy = int((sh - 6*b_px)/2)
        start_idx = bid * 60
        page_results = {}
        detected_count = 0
        
        for r in range(6):
            for c in range(10):
                idx = start_idx + (r * 10 + c)
                if idx >= len(self.char_list): continue
                
                # Padding: 22px (Merkeze odaklan, yanlardan ve çerçeveden uzak dur)
                p = 22 
                roi = warped[sy+r*b_px+p : sy+r*b_px+b_px-p, sx+c*b_px+p : sx+c*b_px+b_px-p]
                
                processed_img = self.process_roi(roi)
                if processed_img is not None:
                    _, buffer = cv2.imencode(".png", processed_img)
                    b64_str = base64.b64encode(buffer).decode('utf-8').replace('\n', '')
                    page_results[self.char_list[idx]] = b64_str
                    detected_count += 1
                    
        return {'harfler': page_results, 'detected': detected_count, 'section_id': bid, 'total_in_section': min(60, len(self.char_list)-start_idx)}, None
        # ... (Marker tespit kodları aynı kalıyor) ...
        # Marker tespiti için grayscale yap
        gray_full = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        
        aruco_dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
        parameters = cv2.aruco.DetectorParameters()
        parameters.adaptiveThreshWinSizeMin = 3
        parameters.adaptiveThreshWinSizeMax = 23
        parameters.adaptiveThreshWinSizeStep = 5
        
        detector = cv2.aruco.ArucoDetector(aruco_dict, parameters)
        
        corners, ids, _ = detector.detectMarkers(gray_full)
        
        # Eğer bulunamazsa kontrast artırıp tekrar dene
        if ids is None or len(ids) < 4:
            clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8,8))
            enhanced = clahe.apply(gray_full)
            corners, ids, _ = detector.detectMarkers(enhanced)
        
        if ids is None or len(ids) < 4:
            return None, f"Yetersiz marker ({0 if ids is None else len(ids)}/4). Lütfen fotoğrafı dik ve net çekin."
        
        ids = ids.flatten()
        
        # 2. Bölüm Tespiti
        if forced_section_id is not None:
            bid = forced_section_id
            start_id = bid * 4
            expected = [(start_id + k) % 50 for k in range(4)]
        else:
            base = int(min(ids))
            bid = base // 4
            start_id = bid * 4
            expected = [start_id, start_id+1, start_id+2, start_id+3]
        
        # 3. Perspektif
        src_points = []
        found_centers = {}
        for idx in range(len(ids)):
            found_centers[ids[idx]] = np.mean(corners[idx][0], axis=0)
            
        missing = []
        for target in expected:
            if target in found_centers: src_points.append(found_centers[target])
            else: missing.append(target)
                
        if missing: return None, f"Bölüm {bid} için markerlar eksik: {missing}"
            
        src = np.float32(src_points)
        scale = 10; sw, sh = 210 * scale, 148 * scale; m = 175
        dst = np.float32([[m, m], [sw-m, m], [m, sh-m], [sw-m, sh-m]])
        M = cv2.getPerspectiveTransform(src, dst)
        warped = cv2.warpPerspective(img, M, (sw, sh))
        
        # 4. Izgara Kesimi
        b_px = 150
        sx = int((sw - 10*b_px)/2)
        sy = int((sh - 6*b_px)/2)
        start_idx = bid * 60
        page_results = {}
        detected_count = 0
        
        for r in range(6):
            for c in range(10):
                idx = start_idx + (r * 10 + c)
                if idx >= len(self.char_list): continue
                
                # Padding'i azalttım (15 -> 8). Kutunun kenarına yazılanlar artık kesilmez.
                p = 8 
                roi = warped[sy+r*b_px+p : sy+r*b_px+b_px-p, sx+c*b_px+p : sx+c*b_px+b_px-p]
                
                processed_img = self.process_roi(roi)
                if processed_img is not None:
                    _, buffer = cv2.imencode(".png", processed_img)
                    b64_str = base64.b64encode(buffer).decode('utf-8').replace('\n', '')
                    page_results[self.char_list[idx]] = b64_str
                    detected_count += 1
                    
        return {'harfler': page_results, 'detected': detected_count, 'section_id': bid, 'total_in_section': min(60, len(self.char_list)-start_idx)}, None

# Varsayılan sistem (3x) - İstek üzerine değişebilir
default_sistem = HarfSistemi(repetition=3)

# --- WEB ROTALARI ---

@app.route('/')
def index():
    font_id = request.args.get('font_id', '')
    user_id = request.args.get('user_id', '')
    return render_template('index.html', font_id=font_id, user_id=user_id)

@app.route('/mobil_yukle.html')
def mobil_page():
    # Statik dosya olarak sunmak yerine template render edebiliriz ama 
    # dosya yapısına göre statik sunum daha kolay olabilir.
    # Şimdilik template klasöründe olduğunu varsayalım ya da direkt static'den okuyalım.
    return send_file('web/mobil_yukle.html')

@app.route('/api/list_fonts')
def list_fonts():
    user_id = request.args.get('user_id')
    fonts = []
    database = init_firebase()
    if not database: return jsonify({"success": False, "error": "Veritabanı bağlantısı yok"})
    
    try:
        # Fontları benzersiz yapmak için dict kullan
        fonts_dict = {}

        # 1. Public Fontları Çek
        try:
            public_fonts = database.collection('fonts').stream()
            for doc in public_fonts:
                d = doc.to_dict()
                fid = d.get('font_id', doc.id)
                # Eğer fontun sahibi bu kullanıcıysa, bunu 'private' olarak işaretleyeceğiz aşağıda
                fonts_dict[fid] = {
                    'id': fid,
                    'name': d.get('font_name') or fid,
                    'type': 'public',
                    'repetition': d.get('repetition', d.get('variation_count', 3)),
                    'char_count': d.get('harf_sayisi', 0),
                    'owner_id': d.get('owner_id', '')
                }
        except Exception as e: print(f"Public font hatası: {e}")

        # 2. Kullanıcının Fontlarını Çek (User ID varsa)
        if user_id:
            # Yöntem A: Users koleksiyonundan çek
            try:
                user_fonts_ref = database.collection('users').document(user_id).collection('fonts').stream()
                for doc in user_fonts_ref:
                    d = doc.to_dict()
                    fid = d.get('font_id', doc.id)
                    fonts_dict[fid] = {
                        'id': fid,
                        'name': d.get('font_name') or fid,
                        'type': 'private',
                        'repetition': d.get('repetition', d.get('variation_count', 3)),
                        'char_count': d.get('harf_sayisi', 0),
                        'owner_id': user_id
                    }
            except: pass

            # Yöntem B: Ana koleksiyonda owner_id araması yap (Garanti Yöntem)
            try:
                owner_query = database.collection('fonts').where('owner_id', '==', user_id).stream()
                for doc in owner_query:
                    d = doc.to_dict()
                    fid = d.get('font_id', doc.id)
                    # Zaten ekliyse güncelle, değilse ekle
                    fonts_dict[fid] = {
                        'id': fid,
                        'name': d.get('font_name') or fid,
                        'type': 'private',
                        'repetition': d.get('repetition', d.get('variation_count', 3)),
                        'char_count': d.get('harf_sayisi', 0),
                        'owner_id': user_id
                    }
            except: pass

        # Dict'i listeye çevir
        fonts = list(fonts_dict.values())
        return jsonify({"success": True, "fonts": fonts})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/add_to_library', methods=['POST'])
def add_to_library():
    try:
        data = request.get_json()
        user_id = data.get('user_id')
        font_id = data.get('font_id')
        
        database = init_firebase()
        if not database: return jsonify({'success': False, 'message': 'Veritabanı yok'}), 500

        # 1. Kaynak fontu bul (Public listesinden)
        source_doc = database.collection('fonts').document(font_id).get()
        if not source_doc.exists:
            return jsonify({'success': False, 'message': 'Font bulunamadı'}), 404
            
        font_data = source_doc.to_dict()
        
        # 2. Kullanıcının listesine kopyala
        # (owner_id'yi değiştirmiyoruz ki orijinal sahibi belli olsun, ama koleksiyona ekliyoruz)
        target_ref = database.collection('users').document(user_id).collection('fonts').document(font_id)
        
        # Kullanıcı kütüphanesine eklendiği için bir işaret koyabiliriz
        font_data['added_from_public'] = True
        font_data['added_at'] = firestore.SERVER_TIMESTAMP
        
        target_ref.set(font_data)
        
        return jsonify({'success': True, 'message': 'Font kütüphaneye eklendi'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/get_assets')
def get_assets():
    font_id = request.args.get('font_id')
    user_id = request.args.get('user_id')
    assets = {}
    database = init_firebase()
    if database and font_id:
        doc = database.collection('fonts').document(font_id).get()
        if not doc.exists and user_id:
            doc = database.collection('users').document(user_id).collection('fonts').document(font_id).get()
        if doc.exists:
            harfler_data = doc.to_dict().get('harfler', {})
            for key, b64 in harfler_data.items():
                base_key = key.rsplit('_', 1)[0] if '_' in key else key
                if base_key not in assets: assets[base_key] = []
                assets[base_key].append(b64)
            return jsonify({"success": True, "assets": assets, "source": "firebase"})
    
    HARFLER_KLASORU = 'static/harfler'
    if os.path.exists(HARFLER_KLASORU):
        for dosya in os.listdir(HARFLER_KLASORU):
            if dosya.endswith('.png'):
                key = dosya.rsplit('_', 1)[0]
                if key not in assets: assets[key] = []
                assets[key].append(dosya)
        if assets: return jsonify({"success": True, "assets": assets, "source": "local"})
    return jsonify({"success": True, "assets": {}, "source": "none", "warning": "Harf yok"}), 200

# --- TARAMA VE UPLOAD ---

@app.route('/process_single', methods=['POST'])
def process_single():
    global init_error
    try:
        data = request.get_json()
        u_id = data.get('user_id')
        f_name = data.get('font_name')
        b64 = data.get('image_base64')
        
        # Repetition parametresini al (Yoksa 3 varsay)
        # Frontend'den gönderilmesi iyi olur ama gönderilmezse varsayılanı kullanırız.
        # Mobil uygulamadan henüz gönderilmiyor, o yüzden 3 varsayıyoruz.
        repetition = int(data.get('variation_count', 3))
        
        # Dinamik sistem oluştur (Eğer varsayılan 3'ten farklıysa)
        current_sistem = default_sistem
        if repetition != 3:
            current_sistem = HarfSistemi(repetition=repetition)

        if not u_id or not b64: return jsonify({'success': False, 'message': 'Eksik veri'}), 400
        
        nparr = np.frombuffer(base64.b64decode(b64), np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        if img is None: return jsonify({'success': False, 'message': 'Resim okunamadı'}), 400

        res, err = current_sistem.process_single_page(img)
        
        if err: return jsonify({'success': False, 'message': err}), 400

        database = init_firebase()
        if database:
            try:
                fid = f"{u_id}_{f_name.replace(' ', '_')}"
                d_ref = database.collection('fonts').document(fid)
                u_ref = database.collection('users').document(u_id).collection('fonts').document(fid)
                
                doc = d_ref.get()
                
                # Toplam karakter sayısını hesapla
                total_chars = len(current_sistem.char_list)
                
                # Yeni veriyi hazırla
                new_harfler = res['harfler']
                new_section = res['section_id']
                
                if not doc.exists:
                    payload = {
                        'harfler': new_harfler, 
                        'harf_sayisi': len(new_harfler), 
                        'sections_completed': [new_section],
                        'owner_id': u_id, 
                        'user_id': u_id, 
                        'font_name': f_name, 
                        'font_id': fid, 
                        'repetition': repetition,
                        'total_expected': total_chars,
                        'created_at': firestore.SERVER_TIMESTAMP
                    }
                    d_ref.set(payload)
                    u_ref.set(payload)
                else:
                    curr = doc.to_dict()
                    h = curr.get('harfler', {})
                    h.update(new_harfler)
                    
                    s = curr.get('sections_completed', [])
                    if new_section not in s: s.append(new_section)
                    s.sort()
                    
                    payload = {
                        'harfler': h, 
                        'harf_sayisi': len(h), 
                        'sections_completed': s, 
                        'font_id': fid
                    }
                    d_ref.update(payload)
                    u_ref.update(payload)
            except Exception as e: print(f"DB Kayıt Hatası: {e}")

        return jsonify({
            'success': True,
            'section_id': res['section_id'],
            'detected_chars': res['detected'],
            'total_chars_found': len(res['harfler']),
            'db_project_id': connected_project_id
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/upload_form', methods=['POST'])
def upload_form():
    # PDF Upload Endpoint'i (Gelecekte PDF'ten ayırma eklenebilir)
    # Şimdilik basitçe process_single'a yönlendirmek zor çünkü PDF çok sayfalı.
    # PDF'i görüntülere ayırıp tek tek process_single çağırmak gerekir.
    # Bu özellik şu an prompt'ta istenmedi ama 'ekle.html'de var. 
    # O yüzden basit bir "Yapım aşamasında" veya PDF split mantığı gerekebilir.
    # Ancak kullanıcı "Telefondan foto çekecek" dediği için şimdilik mobil odaklı gidiyoruz.
    return jsonify({'success': False, 'message': 'PDF yükleme sunucu tarafında henüz aktif değil. Lütfen mobil taramayı kullanın.'})

@app.route('/download', methods=['POST'])
def download():
    try:
        metin = request.form.get('metin', '')
        font_id = request.form.get('font_id')
        user_id = request.form.get('user_id')
        yazi_boyutu = int(request.form.get('yazi_boyutu', 140))
        satir_araligi = int(request.form.get('satir_araligi', 220))
        kelime_boslugu = int(request.form.get('kelime_boslugu', 55))
        jitter = int(request.form.get('jitter', 3))
        murekkep_rengi_str = request.form.get('murekkep_rengi', 'tukenmez')
        paper_type = request.form.get('paper_type', 'cizgili')

        active_harfler = {}
        database = init_firebase()
        if database and font_id:
            doc = database.collection('fonts').document(font_id).get()
            if not doc.exists and user_id:
                doc = database.collection('users').document(user_id).collection('fonts').document(font_id).get()
            
            if doc.exists:
                raw_harfler = doc.to_dict().get('harfler', {})
                for key, b64_data in raw_harfler.items():
                    try:
                        if "," in b64_data: b64_data = b64_data.split(",")[1]
                        img_data = base64.b64decode(b64_data)
                        img = core_generator.Image.open(io.BytesIO(img_data)).convert("RGBA")
                        
                        # Key formatını kontrol et (kucuk_a_1 -> kucuk_a)
                        parts = key.rsplit('_', 1)
                        if len(parts) > 1 and parts[1].isdigit():
                            base_key = parts[0]
                        else:
                            base_key = key
                            
                        if base_key not in active_harfler: active_harfler[base_key] = []
                        active_harfler[base_key].append(img)
                    except: continue

        if not active_harfler:
            HARFLER_KLASORU = 'static/harfler'
            if os.path.exists(HARFLER_KLASORU):
                active_harfler = core_generator.harf_resimlerini_yukle(HARFLER_KLASORU)

        renkler = {'tukenmez':(27,27,29), 'bic_mavi':(0,35,102), 'pilot_mavi':(0,51,153), 'eski_murekkep':(40,60,120), 'kirmizi':(180,20,20), 'lacivert':(24,18,110)}
        murekkep = renkler.get(murekkep_rengi_str, renkler['tukenmez'])

        config = {
            'page_width': 2480, 'page_height': 3508, 'margin_top': 200, 'margin_left': 150, 'margin_right': 150,
            'target_letter_height': yazi_boyutu, 'line_spacing': satir_araligi, 'word_spacing': kelime_boslugu,
            'murekkep_rengi': murekkep, 'opacity': 0.95, 'jitter': jitter, 'paper_type': paper_type, 'line_slope': 5
        }

        sayfalar = core_generator.metni_sayfaya_yaz(metin, active_harfler, config)
        pdf_buffer = core_generator.sayfalari_pdf_olustur(sayfalar)
        
        if pdf_buffer:
            return send_file(pdf_buffer, mimetype='application/pdf', as_attachment=True, download_name='el_yazisi.pdf')
        return "Hata", 500
    except Exception as e:
        traceback.print_exc()
        return str(e), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
