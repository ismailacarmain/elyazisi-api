/**
 * EL YAZISI EDİTÖRÜ - FİNAL MOTOR (SÜPER OPTİMİZE)
 */

const API_URL = 'https://elyazisi-api.onrender.com';

const KARAKTER_HARITASI = {
    'a': 'kucuk_a', 'b': 'kucuk_b', 'c': 'kucuk_c', 'ç': 'kucuk_cc', 'd': 'kucuk_d', 'e': 'kucuk_e', 
    'f': 'kucuk_f', 'g': 'kucuk_g', 'ğ': 'kucuk_gg', 'h': 'kucuk_h', 'ı': 'kucuk_ii', 'i': 'kucuk_i', 
    'j': 'kucuk_j', 'k': 'kucuk_k', 'l': 'kucuk_l', 'm': 'kucuk_m', 'n': 'kucuk_n', 'o': 'kucuk_o', 
    'ö': 'kucuk_oo', 'p': 'kucuk_p', 'r': 'kucuk_r', 's': 'kucuk_s', 'ş': 'kucuk_ss', 't': 'kucuk_t', 
    'u': 'kucuk_u', 'ü': 'kucuk_uu', 'v': 'kucuk_v', 'y': 'kucuk_y', 'z': 'kucuk_z',
    'w': 'kucuk_w', 'q': 'kucuk_q', 'x': 'kucuk_x',
    'A': 'buyuk_a', 'B': 'buyuk_b', 'C': 'buyuk_c', 'Ç': 'buyuk_cc', 'D': 'buyuk_d', 'E': 'buyuk_e', 
    'F': 'buyuk_f', 'G': 'buyuk_g', 'Ğ': 'buyuk_gg', 'H': 'buyuk_h', 'I': 'buyuk_ii', 'İ': 'buyuk_i', 
    'J': 'buyuk_j', 'K': 'buyuk_k', 'L': 'buyuk_l', 'M': 'buyuk_m', 'N': 'buyuk_n', 'O': 'buyuk_o', 
    'Ö': 'buyuk_oo', 'P': 'buyuk_p', 'R': 'buyuk_r', 'S': 'buyuk_s', 'Ş': 'buyuk_ss', 'T': 'buyuk_t', 
    'U': 'buyuk_u', 'Ü': 'buyuk_uu', 'V': 'buyuk_v', 'Y': 'buyuk_y', 'Z': 'buyuk_z',
    'W': 'buyuk_w', 'Q': 'buyuk_q', 'X': 'buyuk_x',
    '0': 'rakam_0', '1': 'rakam_1', '2': 'rakam_2', '3': 'rakam_3', '4': 'rakam_4', 
    '5': 'rakam_5', '6': 'rakam_6', '7': 'rakam_7', '8': 'rakam_8', '9': 'rakam_9',
    '.': 'ozel_nokta', ',': 'ozel_virgul', ':': 'ozel_ikiknokta', ';': 'ozel_noktalivirgul', 
    '?': 'ozel_soru', '!': 'ozel_unlem', '-': 'ozel_tire', '(': 'ozel_parantezac', 
    ')': 'ozel_parantezkapama', '"': 'ozel_tirnak', "'": 'ozel_tektirnak', '[': 'ozel_koseli_ac', 
    ']': 'ozel_koseli_kapa', '{': 'ozel_suslu_ac', '}': 'ozel_suslu_kapa', '/': 'ozel_slash', 
    '\u005C': 'ozel_backslas', '|': 'ozel_pipe', '+': 'ozel_arti', '*': 'ozel_carpi', 
    '=': 'ozel_esit', '<': 'ozel_kucuktur', '>': 'ozel_buyuktur', '%': 'ozel_yuzde', 
    '^': 'ozel_sapka', '#': 'ozel_diyez', '~': 'ozel_yaklasik', '_': 'ozel_alt_tire',
    '@': 'ozel_at', '$': 'ozel_dolar', '\u20AC': 'ozel_euro', '\u20BA': 'ozel_tl', '&': 'ozel_ampersand'
};

class FinalHandwritingEditor {
    constructor(canvasId) {
        this.canvas = document.getElementById(canvasId);
        this.ctx = this.canvas.getContext('2d', { alpha: false, desynchronized: true });
        
        this.WIDTH = 2480;
        this.HEIGHT = 3508;
        this.canvas.width = this.WIDTH;
        this.canvas.height = this.HEIGHT;
        
        this.config = {
            marginTop: 250, marginLeft: 250, marginRight: 150,
            lineHeight: 220, letterScale: 140, baselineOffset: 10,
            wordSpacing: 55, letterSpacing: 3, boldness: 0,
            jitter: 5, lineSlope: 0, paperType: 'duz',
            inkColor: 'tukenmez', showLines: true
        };
        
        this.textContent = "";
        this.cursorIndex = 0;
        this.customLineSlopes = {}; 
        this.customLineCurves = {}; 
        this.isExporting = false;
        this.isManualNav = false;
        this.customPaperImage = null;
        this.customInkColor = null;
        
        this.history = [];
        this.historyStep = -1;
        this.isUndoing = false;
        
        this.currentPage = 0;
        this.totalPages = 1;
        this.linesPerPage = 12; 
        this.pendingPageCursor = null;

        this.assets = {};
        this.assetsLoaded = false;
        this.cursorVisible = true;
        this.cursorPos = { x: 250, y: 250 };
        this.charPositions = [];
        
        this.charTypeCache = {}; 
        this.renderRequested = false;
        
        // Background Cache
        this.backgroundCanvas = document.createElement('canvas');
        this.backgroundCanvas.width = this.WIDTH;
        this.backgroundCanvas.height = this.HEIGHT;
        this.backgroundCached = false;
    }
    
    async loadAssets(userId, progressCallback) {
        try {
            const urlParams = new URLSearchParams(window.location.search);
            const fontId = urlParams.get('font_id') || '';
            const uid = userId || urlParams.get('user_id') || '';
            console.log(`Loading assets for Font: ${fontId}, User: ${uid}`);
            
            const apiUrl = `${API_URL}/api/get_assets?font_id=${fontId}&user_id=${uid}`;
            const response = await fetch(apiUrl);
            const data = await response.json();
            
            if (!data.success) {
                console.error("Asset yükleme hatası:", data.error);
                return false;
            }

            const assetMap = data.assets || {};
            const source = data.source;
            const allKeys = Object.keys(assetMap);
            const total = allKeys.length;
            let loaded = 0;
            
            this.assets = {}; // Önceki assetleri temizle
            
            for (const [key, files] of Object.entries(assetMap)) {
                // key burada 'kucuk_a' gibi gelir (app.py rsplit sayesinde)
                this.assets[key] = [];
                
                // files bir arraydir. 1x ise length=1, 3x ise length=3 olur.
                for (const fileData of files) {
                    try {
                        let src;
                        if (source === 'firebase') {
                            // URL kontrolü (http/https) veya Base64 kontrolü
                            if (fileData.startsWith('http')) src = fileData;
                            else if (fileData.startsWith('data:image')) src = fileData;
                            else src = `data:image/png;base64,${fileData}`;
                        } else {
                            src = `${API_URL}/static/harfler/${fileData}`;
                        }
                        
                        const img = await this.loadImage(src);
                        this.assets[key].push(img);
                    } catch (err) {
                        console.warn(`Resim yüklenemedi: ${key}`, err);
                    }
                }
                loaded++;
                if (progressCallback && total > 0) progressCallback(loaded / total);
            }
            
            console.log(`Assets loaded: ${Object.keys(this.assets).length} keys found.`);
            this.assetsLoaded = true;
            try {
                const overrides = JSON.parse(localStorage.getItem('font_overrides_' + fontId) || '{}');
                for (const [k, b64] of Object.entries(overrides)) { await this.overrideAsset(k, b64); }
            } catch(e) {}
            
            this.render();
            return true;
        } catch (error) { 
            console.error("LoadAssets Critical Error:", error);
            this.assetsLoaded = true; 
            return false; 
        }
    }
    
    loadImage(src) {
        return new Promise((resolve, reject) => {
            const img = new Image();
            img.crossOrigin = "anonymous";
            img.onload = () => resolve(img);
            img.onerror = reject;
            img.src = src;
        });
    }

    async overrideAsset(key, base64Img) {
        try {
            const parts = key.split('_');
            const idx = parseInt(parts.pop()) - 1;
            const baseKey = parts.join('_');
            const img = await this.loadImage(base64Img);
            if (!this.assets[baseKey]) this.assets[baseKey] = [];
            if (idx >= 0 && idx < this.assets[baseKey].length) this.assets[baseKey][idx] = img;
            else this.assets[baseKey].push(img);
            this.render();
        } catch(e) {}
    }

    requestRender() {
        if (!this.renderRequested) {
            this.renderRequested = true;
            requestAnimationFrame(() => {
                this.render();
                this.renderRequested = false;
            });
        }
    }

    render() {
        try {
            this.ctx.clearRect(0, 0, this.WIDTH, this.HEIGHT);
            this.drawBackground();
            this.drawText();
            this.drawCursor();
        } catch(e) { console.error(e); }
    }

    drawBackground() {
        if (this.backgroundCached && !this.customPaperImage) {
            this.ctx.drawImage(this.backgroundCanvas, 0, 0);
            return;
        }

        const bgCtx = this.backgroundCanvas.getContext('2d');
        bgCtx.fillStyle = '#ffffff';
        bgCtx.fillRect(0, 0, this.WIDTH, this.HEIGHT);

        if (this.customPaperImage) {
            bgCtx.drawImage(this.customPaperImage, 0, 0, this.WIDTH, this.HEIGHT);
        } else if (this.config.paperType !== 'duz') {
            bgCtx.strokeStyle = 'rgba(135, 206, 250, 0.4)';
            bgCtx.lineWidth = 2;
            let y = this.config.marginTop;
            while (y < this.HEIGHT - 100) {
                bgCtx.beginPath(); bgCtx.moveTo(0, y); bgCtx.lineTo(this.WIDTH, y); bgCtx.stroke();
                y += this.config.lineHeight;
            }
            if (this.config.paperType === 'kareli') {
                let x = this.config.marginLeft;
                const gridSize = this.config.lineHeight; 
                while (x < this.WIDTH) { bgCtx.beginPath(); bgCtx.moveTo(x, 0); bgCtx.lineTo(x, this.HEIGHT); bgCtx.stroke(); x += gridSize; }
                x = this.config.marginLeft;
                while (x > 0) { x -= gridSize; bgCtx.beginPath(); bgCtx.moveTo(x, 0); bgCtx.lineTo(x, this.HEIGHT); bgCtx.stroke(); }
            }
        }
        
        bgCtx.save();
        bgCtx.font = "bold 40px Arial";
        bgCtx.fillStyle = "rgba(0, 0, 0, 0.1)"; 
        bgCtx.textAlign = "right";
        bgCtx.fillText(`Sayfa ${this.currentPage + 1} / ${this.totalPages}`, this.WIDTH - 100, this.HEIGHT - 100);
        bgCtx.restore();

        this.ctx.drawImage(this.backgroundCanvas, 0, 0);
        if (!this.customPaperImage) this.backgroundCached = true;
    }
    
    drawText() {
        this.charPositions = [];
        let x = this.config.marginLeft;
        let y = this.config.marginTop;
        const maxX = this.WIDTH - this.config.marginRight;
        let globalLineIndex = 0;
        let cursorLine = 0; 

        if (this.pendingPageCursor !== null) {
            this.currentPage = this.pendingPageCursor;
            this.pendingPageCursor = null; 
            this.isManualNav = true; 
        }

        const startLine = this.currentPage * this.linesPerPage;
        const endLine = startLine + this.linesPerPage;
        
        for (let i = 0; i < this.textContent.length; i++) {
            if (i === this.cursorIndex) cursorLine = globalLineIndex;
            const char = this.textContent[i];
            
            if (char === '\n') {
                this.charPositions.push({ x: -100, y: -100, width: 0, height: 0, line: globalLineIndex });
                x = this.config.marginLeft;
                globalLineIndex++;
                continue;
            }
            
            const img = this.getLetterImage(char, i);
            if (!img) {
                const w = this.config.letterScale * (char === ' ' ? 0.4 : 0.5);
                if (globalLineIndex >= startLine && globalLineIndex < endLine) {
                    const ry = this.config.marginTop + ((globalLineIndex - startLine) * this.config.lineHeight);
                    this.charPositions.push({ x, y: ry, width: w, height: this.config.letterScale });
                } else {
                    this.charPositions.push({ x: -100, y: -100, width: 0, height: 0 });
                }
                x += w;
                if (x > maxX) { x = this.config.marginLeft; globalLineIndex++; }
                continue;
            }
            
            // Karakter Boyutlandırma ve Hizalama (Geliştirilmiş Denge)
            let drawScale = this.config.letterScale;
            let drawBaselineOffset = this.config.baselineOffset;
            
            // Gruplar
            const descenders = "gjpqyğ"; 
            const cedillas = "çş";       
            const ascenders = "bdfhklt"; 
            const topPunctuation = "\"'";     // Üstte durması gerekenler
            const midPunctuation = "-";       // Ortada durması gerekenler
            const bottomPunctuation = ".,";   // Altta durması gerekenler (Nokta, virgül)
            const otherPunctuation = ":;!?()[]{} /\\|@$€₺&";
            const smalls = "aceimnorsuvwxziı+*=< >%^#~"; 

            if (bottomPunctuation.includes(char)) {
                // Nokta ve virgül çok küçük ve en altta
                drawScale = this.config.letterScale * 0.20; 
                drawBaselineOffset = this.config.baselineOffset;
            } 
            else if (topPunctuation.includes(char)) {
                // Tırnak işaretleri en üstte
                drawScale = this.config.letterScale * 0.35;
                drawBaselineOffset -= (this.config.letterScale * 0.65); // Yukarı taşı
            }
            else if (midPunctuation.includes(char)) {
                // Tire tam ortada ve küçük
                drawScale = this.config.letterScale * 0.22;
                drawBaselineOffset -= (this.config.letterScale * 0.35); // Merkeze taşı
            }
            else if (otherPunctuation.includes(char)) {
                drawScale = this.config.letterScale * 0.85; 
            }
            else if (descenders.includes(char)) {
                drawScale = this.config.letterScale * 0.95; 
                drawBaselineOffset += (this.config.letterScale * 0.25); 
            } 
            else if (cedillas.includes(char)) {
                drawScale = this.config.letterScale * 0.92;
                drawBaselineOffset += (this.config.letterScale * 0.15);
            }
            else if (smalls.includes(char)) {
                drawScale = this.config.letterScale * 0.75; 
            } 
            else if (ascenders.includes(char)) {
                drawScale = this.config.letterScale * 0.95; 
            } 
            else {
                drawScale = this.config.letterScale;
            }

            const layoutWidth = img.width * (drawScale / img.height);
            if (x + layoutWidth > maxX) { x = this.config.marginLeft; globalLineIndex++; if (i === this.cursorIndex) cursorLine = globalLineIndex; }
            
            if (globalLineIndex >= startLine && globalLineIndex < endLine) {
                const relLine = globalLineIndex - startLine;
                const ry = this.config.marginTop + (relLine * this.config.lineHeight);
                const curve = this.customLineCurves[globalLineIndex] || 0;
                const archY = Math.sin(Math.max(0, Math.min(1, (x - this.config.marginLeft) / (this.WIDTH - 400))) * Math.PI) * (curve * 10); 
                const drawY = ry + archY + drawBaselineOffset - drawScale;
                
                this.ctx.save();
                if (this.config.inkColor === 'custom' && this.customInkColor) {
                    this.ctx.shadowColor = this.customInkColor; this.ctx.shadowBlur = 0; this.ctx.shadowOffsetX = 10000;
                    this.drawBoldImage(img, Math.floor(x - 10000), Math.floor(drawY), Math.floor(layoutWidth), Math.floor(drawScale));
                } else {
                    this.drawBoldImage(img, Math.floor(x), Math.floor(drawY), Math.floor(layoutWidth), Math.floor(drawScale));
                }
                this.ctx.restore();
                this.charPositions.push({ x, y: drawY, width: layoutWidth, height: drawScale });
            } else {
                this.charPositions.push({ x: -100, y: -100, width: 0, height: 0 });
            }
            x += layoutWidth + this.config.letterSpacing;
        }
        
        if (this.cursorIndex >= this.textContent.length) cursorLine = globalLineIndex;
        const targetPage = Math.floor(cursorLine / this.linesPerPage);
        
        if (!this.isManualNav && targetPage !== this.currentPage && !this.isUndoing) {
            this.currentPage = targetPage;
            setTimeout(() => this.requestRender(), 0);
        }

        this.totalPages = Math.max(1, Math.ceil((globalLineIndex + 1) / this.linesPerPage));
        if (window.updatePageIndicator) window.updatePageIndicator();
        if (window.updateLineSettingsUI) window.updateLineSettingsUI(globalLineIndex + 1);
        this.updateCursorPosition();
    }

    getLetterImage(char, index) {
        let key = this.charTypeCache[char];
        if (!key) {
            key = KARAKTER_HARITASI[char] || 'NONE';
            this.charTypeCache[char] = key;
        }
        
        if (key === 'NONE' || !this.assets[key] || this.assets[key].length === 0) {
            return null;
        }

        const variants = this.assets[key];
        
        // El yazısı doğallığı için varyasyon seçimi:
        // 'index' kullanılarak her pozisyondaki aynı harfin farklı varyasyon alması sağlanır.
        // Asal sayılarla çarpmak (31, 17 vb.) rastgelelik hissini artırır.
        const variantIndex = (index * 31 + char.charCodeAt(0)) % variants.length;
        
        return variants[variantIndex];
    }

    drawBoldImage(img, x, y, width, height) {
        // Koordinatları tam sayıya yuvarla (Performans için kritik)
        x = Math.floor(x);
        y = Math.floor(y);
        width = Math.floor(width);
        height = Math.floor(height);

        const b = this.config.boldness;
        this.ctx.drawImage(img, x, y, width, height); 
        if (b > 0) {
            const a = this.ctx.globalAlpha; this.ctx.globalAlpha = 0.5;
            this.ctx.drawImage(img, x + b, y, width, height);
            this.ctx.drawImage(img, x, y + b, width, height);
            this.ctx.drawImage(img, x + b, y + b, width, height);
            this.ctx.globalAlpha = a;
        }
    }

    updateCursorPosition() {
        if (this.cursorIndex <= 0) {
            this.cursorPos.x = this.config.marginLeft;
            this.cursorPos.y = this.config.marginTop;
        } else {
            const pos = this.charPositions[this.cursorIndex - 1];
            if (pos && pos.x !== -100) {
                this.cursorPos.x = pos.x + pos.width + 2;
                this.cursorPos.y = pos.y;
            }
        }
    }

    drawCursor() {
        if (!this.cursorVisible || this.isExporting) return;
        this.ctx.strokeStyle = '#3498db'; this.ctx.lineWidth = 4;
        this.ctx.beginPath(); this.ctx.moveTo(this.cursorPos.x, this.cursorPos.y);
        this.ctx.lineTo(this.cursorPos.x, this.cursorPos.y + this.config.letterScale);
        this.ctx.stroke();
    }

    getCursorIndexFromCoords(x, y) {
        if (!this.charPositions.length) return 0;
        let closest = 0, minDist = Infinity;
        for (let i = 0; i < this.charPositions.length; i++) {
            const pos = this.charPositions[i];
            if (pos.x === -100) continue;
            const dist = Math.hypot(x - (pos.x + pos.width/2), y - (pos.y + (this.config.letterScale/2)));
            if (dist < minDist) { minDist = dist; closest = x > (pos.x + pos.width/2) ? i + 1 : i; }
        }
        return closest;
    }

    setText(t) { 
        this.textContent = t; 
        this.isManualNav = false; 
        this.requestRender(); 
    }
    setCursorIndex(i) { 
        this.cursorIndex = i; 
        this.isManualNav = false; 
        this.updateCursorPosition(); 
        this.requestRender(); 
    }
    setMarginTop(v) { this.config.marginTop = v; this.backgroundCached = false; this.requestRender(); }
    setLineHeight(v) { this.config.lineHeight = v; this.backgroundCached = false; this.requestRender(); }
    setLetterScale(v) { this.config.letterScale = v; this.requestRender(); }
    setLetterSpacing(v) { this.config.letterSpacing = v; this.requestRender(); }
    setWordSpacing(v) { this.config.wordSpacing = v; this.requestRender(); }
    setBoldness(v) { this.config.boldness = v; this.requestRender(); }
    setJitter(v) { this.config.jitter = v; this.requestRender(); }
    setPaperType(v) { this.config.paperType = v; this.backgroundCached = false; this.requestRender(); }
    setCustomPaper(url) { this.loadImage(url).then(img => { this.customPaperImage = img; this.backgroundCached = false; this.requestRender(); }); }
    setInkColor(v) { 
        this.config.inkColor = v; 
        const colorMap = { 'bic_mavi': '#002366', 'pilot_mavi': '#003399', 'eski_murekkep': '#283c78', 'kirmizi': '#b31414', 'kursun': '#555555' };
        if (colorMap[v]) this.setCustomInkColor(colorMap[v]);
        else { this.customInkColor = null; this.canvas.style.filter = 'none'; this.requestRender(); }
    }
    setCustomInkColor(hex) { this.customInkColor = hex; this.config.inkColor = 'custom'; this.requestRender(); }
    setSpecificLineCurve(idx, val) { this.customLineCurves[idx] = val; this.requestRender(); }
    getLineCurve(idx) { return this.customLineCurves[idx] || 0; }
    prevPage() { if (this.currentPage > 0) { this.pendingPageCursor = this.currentPage - 1; this.requestRender(); } }
    nextPage() { if (this.currentPage < this.totalPages - 1) { this.pendingPageCursor = this.currentPage + 1; this.requestRender(); } }
    
    exportPNG() {
        this.isExporting = true; this.render();
        setTimeout(() => {
            const link = document.createElement('a');
            link.download = `el_yazisi_${Date.now()}.png`; link.href = this.canvas.toDataURL('image/png', 1.0);
            link.click(); this.isExporting = false; this.render();
        }, 50);
    }

    async exportPDF() {
        const urlParams = new URLSearchParams(window.location.search);
        const fontId = urlParams.get('font_id') || '', userId = urlParams.get('user_id') || '';
        const form = document.createElement('form');
        form.method = 'POST'; form.action = `${API_URL}/download`; form.target = '_blank';
        const params = { metin: this.textContent, font_id: fontId, user_id: userId, yazi_boyutu: this.config.letterScale, satir_araligi: this.config.lineHeight, kelime_boslugu: this.config.wordSpacing, kalinlik: this.config.boldness, jitter: this.config.jitter, paper_type: this.config.paperType, murekkep_rengi: this.config.inkColor };
        for (const [k, v] of Object.entries(params)) {
            const input = document.createElement('input'); input.type = 'hidden'; input.name = k; input.value = v;
            form.appendChild(input);
        }
        document.body.appendChild(form); form.submit(); document.body.removeChild(form);
    }
}

window.FinalHandwritingEditor = FinalHandwritingEditor;