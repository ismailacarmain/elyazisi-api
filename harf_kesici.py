import cv2
import numpy as np
import os
import shutil

class HarfSistemi:
    def __init__(self, repetition=3, output_folder="harfler"):
        self.output = output_folder
        self.repetition = repetition
        self.char_list = []
        
        # --- KARAKTER LİSTESİ ---
        lowers = "abcçdefgğhıijklmnoöpqrsştuüvwxyz"
        uppers = "ABCÇDEFGĞHIİJKLMNOÖPQRSŞTUÜVWXYZ"
        digits = "0123456789"
        symbols_str = ".,:;?!-_\"'()[]{}/\\|+*=< >%^~@$€₺&#"
        symbols_str = symbols_str.replace(" ", "")
        
        symbols = ""
        seen = set()
        for char in symbols_str:
            if char not in seen:
                symbols += char
                seen.add(char)
        
        sym_map = {
            ".": "nokta", ",": "virgul", ":": "iki_nokta", ";": "noktali_virgul", 
            "?": "soru", "!": "unlem", "-": "tire", "_": "alt_tire",
            "\"": "cift_tirnak", "'": "tek_tirnak", 
            "(": "parantez_ac", ")": "parantez_kapat",
            "[": "koseli_parantez_ac", "]": "koseli_parantez_kapat",
            "{": "suslu_parantez_ac", "}": "suslu_parantez_kapat",
            "/": "slash", "\\": "ters_slash", "|": "dikey_cizgi",
            "+": "arti", "*": "yildiz", "=": "esittir",
            "<": "kucuktur", ">": "buyuktur",
            "%": "yuzde", "^": "sapka", "~": "yaklasik",
            "@": "at_isareti", "$": "dolar", "€": "euro", "₺": "tl",
            "&": "ve_isareti", "#": "kare"
        }
        
        for char in lowers:
            safe = sym_map.get(char, char)
            for i in range(1, self.repetition + 1):
                self.char_list.append(f"küçük_{safe}_{i}")
                
        for char in uppers:
            safe = sym_map.get(char, char)
            for i in range(1, self.repetition + 1):
                self.char_list.append(f"büyük_{safe}_{i}")
                
        for char in digits:
            for i in range(1, self.repetition + 1):
                self.char_list.append(f"rakam_{char}_{i}")
                
        for char in symbols:
            safe = sym_map.get(char, f"sembol_{ord(char)}")
            for i in range(1, self.repetition + 1):
                self.char_list.append(f"{safe}_{i}")

    def clean(self):
        if os.path.exists(self.output): shutil.rmtree(self.output)
        os.makedirs(self.output)

    def crop_tight(self, binary_img):
        coords = cv2.findNonZero(binary_img)
        if coords is None: return None
        x, y, w, h = cv2.boundingRect(coords)
        return binary_img[y:y+h, x:x+w]

    def process_roi(self, roi):
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (3,3), 0)
        thresh = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 25, 12)
        kernel = np.ones((2,2), np.uint8)
        thresh = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel)
        tight = self.crop_tight(thresh)
        if tight is None: return None
        h, w = tight.shape
        rgba = np.zeros((h, w, 4), dtype=np.uint8)
        rgba[:,:,3] = tight
        return rgba

    def run(self, img_path, section_index=None):
        img = cv2.imread(img_path)
        if img is None: return
        
        dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
        params = cv2.aruco.DetectorParameters()
        det = cv2.aruco.ArucoDetector(dict, params)
        corners, ids, _ = det.detectMarkers(img)
        
        if ids is None or len(ids) < 4:
            print(f"Uyarı: {img_path} içinde yeterli marker bulunamadı.")
            return
        
        ids = ids.flatten()
        
        # --- BÖLÜM TESPİTİ VE MARKER EŞLEŞTİRME ---
        if section_index is not None:
            bid = section_index
            # Beklenen marker ID'lerini hesapla (Mod 50 ile)
            start_id = bid * 4
            expected_ids = [(start_id + i) % 50 for i in range(4)]
            # expected_ids[0] -> Sol-Üst, [1]->Sağ-Üst, [2]->Sol-Alt, [3]->Sağ-Alt
        else:
            # Otomatik mod (sadece tek sayfa veya unique markerlar için)
            base = min(ids)
            bid = base // 4
            expected_ids = [base, base+1, base+2, base+3]

        # Perspektif için köşe noktalarını bul
        found_centers = {}
        for i in range(len(ids)):
            curr_id = ids[i]
            # Algılanan ID, beklenenlerden biriyse kaydet
            if curr_id in expected_ids:
                c = corners[i][0]
                center = np.mean(c, axis=0)
                found_centers[curr_id] = center
        
        # Eksik marker kontrolü
        missing = [eid for eid in expected_ids if eid not in found_centers]
        if missing:
            print(f"Eksik markerlar (Beklenen: {expected_ids}, Eksik: {missing}) - {img_path}")
            return

        # Sıralama: Sol-Üst, Sağ-Üst, Sol-Alt, Sağ-Alt
        src = np.float32([
            found_centers[expected_ids[0]], 
            found_centers[expected_ids[1]], 
            found_centers[expected_ids[2]], 
            found_centers[expected_ids[3]]
        ])
        
        scale = 10
        sw, sh = 210 * scale, 148 * scale
        m = 175
        
        dst = np.float32([
            [m, m], 
            [sw-m, m], 
            [m, sh-m], 
            [sw-m, sh-m]
        ])
        
        matrix = cv2.getPerspectiveTransform(src, dst)
        warped = cv2.warpPerspective(img, matrix, (sw, sh))
        
        # --- KESİM ---
        b_px = 150
        sx = int((sw - 10*b_px)/2)
        sy = int((sh - 6*b_px)/2)
        
        start_idx = bid * 60 
        
        for r in range(6):
            for c in range(10):
                idx = start_idx + (r * 10 + c)
                if idx >= len(self.char_list): 
                    continue
                
                p = 15
                roi = warped[sy+r*b_px+p : sy+r*b_px+b_px-p, sx+c*b_px+p : sx+c*b_px+b_px-p]
                res = self.process_roi(roi)
                
                if res is not None:
                    fname = self.char_list[idx]
                    save_path = os.path.join(self.output, f"{fname}.png")
                    is_success, buffer = cv2.imencode(".png", res)
                    if is_success:
                        with open(save_path, "wb") as f:
                            f.write(buffer)

if __name__ == "__main__":
    pass
