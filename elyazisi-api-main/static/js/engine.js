/**
 * EL YAZISI EDİTÖRÜ - FİNAL MOTOR (REFACTORED)
 * 
 * ÖZELLİKLER:
 * ✅ 2480x3508 (A4 300 DPI) yüksek çözünürlük
 * ✅ Kalınlık algoritması (offset çizim)
 * ✅ State-based (Word mantığı - Textarea Driven)
 * ✅ Smart Scaling (Akıllı Boyutlandırma)
 */

class FinalHandwritingEditor {
    constructor(canvasId) {
        this.canvas = document.getElementById(canvasId);
        this.ctx = this.canvas.getContext('2d');
        
        // A4 300 DPI YÜKSEK ÇÖZÜNÜRLÜK
        this.WIDTH = 2480;
        this.HEIGHT = 3508;
        
        this.canvas.width = this.WIDTH;
        this.canvas.height = this.HEIGHT;
        
        // CONFIG
        this.config = {
            marginTop: 250,
            marginLeft: 250,
            marginRight: 150,
            lineHeight: 220,
            letterScale: 140,
            baselineOffset: 10,
            wordSpacing: 55,
            letterSpacing: 3,
            boldness: 0,
            jitter: 5,
            lineSlope: 5,
            paperType: 'cizgili',
            inkColor: 'tukenmez',
            showLines: true
        };
        
        // STATE
        this.textContent = "";
        this.cursorIndex = 0;
        this.customLineSlopes = {}; 
        
        // ASSETS
        this.assets = {};
        this.assetsLoaded = false;
        
        // CURSOR
        this.cursorVisible = true;
        this.cursorInterval = null;
        this.cursorPos = { x: this.config.marginLeft, y: this.config.marginTop };
        
        // HIT TESTING
        this.charPositions = [];
    }
    
    async loadAssets(progressCallback) {
        try {
            const urlParams = new URLSearchParams(window.location.search);
            const fontId = urlParams.get('font_id') || '';
            const userId = urlParams.get('user_id') || ''; // Firebase Auth ile override edilecek
            
            const apiUrl = `/api/get_assets?font_id=${fontId}&user_id=${userId}`;
            const response = await fetch(apiUrl);
            const data = await response.json();
            
            const assetMap = data.assets || {};
            const source = data.source;
            const allKeys = Object.keys(assetMap);
            const total = allKeys.length;
            let loaded = 0;
            
            for (const [key, files] of Object.entries(assetMap)) {
                this.assets[key] = [];
                for (const fileData of files) {
                    try {
                        let src = "";
                        if (source === 'firebase') {
                            src = fileData.startsWith('data:image') ? fileData : `data:image/png;base64,${fileData}`;
                        } else {
                            src = `/static/harfler/${fileData}`;
                        }
                        const img = await this.loadImage(src);
                        this.assets[key].push(img);
                    } catch (err) {}
                }
                loaded++;
                if (progressCallback && total > 0) progressCallback(loaded / total);
            }
            
            this.assetsLoaded = true;
            this.startCursorBlink();
            this.render();
            return true;
        } catch (error) {
            console.error('Yükleme hatası:', error);
            this.assetsLoaded = true;
            return false;
        }
    }
    
    loadImage(src) {
        return new Promise((resolve, reject) => {
            const img = new Image();
            img.onload = () => resolve(img);
            img.onerror = reject;
            img.src = src;
        });
    }
    
    /**
     * DIŞARIDAN METİN GÜNCELLEME (Textarea'dan gelir)
     */
    setText(text) {
        this.textContent = text;
        this.render();
    }

    /**
     * DIŞARIDAN İMLEÇ POZİSYONU GÜNCELLEME
     */
    setCursorIndex(index) {
        this.cursorIndex = index;
        this.updateCursorPosition();
        // İmleci anlık güncellemek için tekrar çizmeye gerek yok, sadece cursor çizilebilir
        // Ancak blink interval zaten render çağırıyor.
        // Biz yine de anlık tepki için render çağıralım.
        this.render();
    }
    
    getLetterImage(char) {
        const special = {
            '.': 'ozel_nokta', ',': 'ozel_virgul', ':': 'ozel_ikiknokta',
            ';': 'ozel_noktalivirgul', '?': 'ozel_soru', '!': 'ozel_unlem',
            '-': 'ozel_tire', '(': 'ozel_parantezac', ')': 'ozel_parantezkapama'
        };
        
        let key = null;
        if (special[char]) key = special[char];
        else if (/[0-9]/.test(char)) key = `rakam_${char}`;
        else if (/[A-ZÇĞİÖŞÜ]/.test(char)) key = `buyuk_${char}`;
        else if (/[a-zçğıöşü]/.test(char)) key = `kucuk_${char}`;
        
        if (key && this.assets[key] && this.assets[key].length > 0) {
            return this.assets[key][Math.floor(Math.random() * this.assets[key].length)];
        }
        return null;
    }
    
    render() {
        this.ctx.clearRect(0, 0, this.WIDTH, this.HEIGHT);
        this.ctx.fillStyle = '#ffffff';
        this.ctx.fillRect(0, 0, this.WIDTH, this.HEIGHT);
        
        this.drawBackground();
        this.drawText();
        this.drawCursor();
    }
    
    drawBackground() {
        if (!this.config.showLines || this.config.paperType === 'duz') return;
        this.ctx.strokeStyle = 'rgba(135, 206, 250, 0.4)';
        this.ctx.lineWidth = 2;
        let y = this.config.marginTop;
        while (y < this.HEIGHT - 100) {
            this.ctx.beginPath(); this.ctx.moveTo(0, y); this.ctx.lineTo(this.WIDTH, y); this.ctx.stroke();
            y += this.config.lineHeight;
        }
        if (this.config.paperType === 'kareli') {
            let x = this.config.marginLeft;
            const gridSize = this.config.lineHeight; 
            while (x < this.WIDTH) {
                this.ctx.beginPath(); this.ctx.moveTo(x, 0); this.ctx.lineTo(x, this.HEIGHT); this.ctx.stroke();
                x += gridSize;
            }
            x = this.config.marginLeft;
            while (x > 0) {
                x -= gridSize;
                this.ctx.beginPath(); this.ctx.moveTo(x, 0); this.ctx.lineTo(x, this.HEIGHT); this.ctx.stroke();
            }
        }
    }
    
    drawText() {
        this.charPositions = [];
        let x = this.config.marginLeft;
        let y = this.config.marginTop;
        const maxX = this.WIDTH - this.config.marginRight;
        
        let lineIndex = 0;
        const getLineSlope = (idx) => {
            if (this.customLineSlopes[idx] !== undefined) return this.customLineSlopes[idx] * 0.0015;
            return Math.sin(idx * 123.456) * (this.config.lineSlope * 0.0015);
        };
        const getLineYOffset = (idx) => Math.cos(idx * 543.21) * (this.config.lineSlope * 1.5);   
        
        for (let i = 0; i < this.textContent.length; i++) {
            const char = this.textContent[i];
            
            // Satır başı
            if (char === '\n' || x === this.config.marginLeft) {
                if (char === '\n') {
                    this.charPositions.push({ x, y: y - this.config.letterScale, width: 0, height: this.config.letterScale });
                    x = this.config.marginLeft;
                    y += this.config.lineHeight;
                    lineIndex++;
                    continue;
                }
            }
            
            // Boşluk
            if (char === ' ') {
                this.charPositions.push({ x, y: y - this.config.letterScale, width: this.config.wordSpacing, height: this.config.letterScale });
                x += this.config.wordSpacing;
                if (x > maxX) {
                    x = this.config.marginLeft;
                    y += this.config.lineHeight;
                    lineIndex++;
                }
                continue;
            }
            
            const img = this.getLetterImage(char);
            if (!img) {
                this.charPositions.push({ x, y, width: 0, height: 0 });
                continue;
            }
            
            // Layout
            const layoutScale = this.config.letterScale / img.height;
            const layoutWidth = img.width * layoutScale;
            const layoutHeight = this.config.letterScale;

            if (x + layoutWidth > maxX) {
                x = this.config.marginLeft;
                y += this.config.lineHeight;
                lineIndex++;
            }
            
            // SMART SCALING
            const chaosLevel = this.config.jitter;
            let typeScale = 1.0;
            let typeYOffset = 0;

            if (/[A-ZÇĞİÖŞÜ]/.test(char)) typeScale = 1.0; 
            else if (/[bdfhklt]/.test(char)) typeScale = 0.95;
            else if (/[gjpqy]/.test(char)) { typeScale = 0.90; typeYOffset = this.config.letterScale * 0.25; }
            else if (/[acemnorsuvwxzıüğişöç]/.test(char)) { typeScale = 0.65; typeYOffset = this.config.letterScale * 0.15; }
            else if (/[.,:;]/.test(char)) { typeScale = 0.25; typeYOffset = this.config.letterScale * 0.40; }
            else if (/['"`^]/.test(char)) { typeScale = 0.25; typeYOffset = -this.config.letterScale * 0.35; }
            else if (/[-+*=\/]/.test(char)) { typeScale = 0.50; typeYOffset = this.config.letterScale * 0.10; }

            const sizeNoise = (Math.sin(i * 4321) * 0.01 * chaosLevel); 
            const drawScale = this.config.letterScale * typeScale * (1 + sizeNoise);
            const drawImgScale = drawScale / img.height;
            const drawWidth = img.width * drawImgScale;
            const drawHeight = drawScale;
            
            const slope = getLineSlope(lineIndex);
            const lineOffset = getLineYOffset(lineIndex);
            const relativeX = x - this.config.marginLeft;
            const slopeY = relativeX * slope;
            
            const angle = (Math.sin(i * 987.65) * 0.003 * chaosLevel);
            const jitterY = Math.cos(i * 135.79) * (chaosLevel * 0.4);
            
            const baseY = y + this.config.baselineOffset + slopeY + lineOffset + jitterY;
            const drawY = baseY - drawHeight + typeYOffset;
            
            this.ctx.save();
            const centerX = x + layoutWidth / 2;
            const centerY = drawY + drawHeight / 2;
            
            this.ctx.translate(centerX, centerY);
            this.ctx.rotate(angle);
            this.ctx.translate(-centerX, -centerY);
            
            const drawX = x + (layoutWidth - drawWidth) / 2;
            
            this.drawBoldImage(img, drawX, drawY, drawWidth, drawHeight);
            this.ctx.restore();
            
            this.charPositions.push({ x, y: drawY, width: layoutWidth, height: layoutHeight });
            
            x += layoutWidth + this.config.letterSpacing;
        }
        
        this.visualLineCount = lineIndex + 1;
        this.updateCursorPosition();
    }

    drawBoldImage(img, x, y, width, height) {
        const boldness = this.config.boldness;
        if (boldness === 0) {
            this.ctx.drawImage(img, x, y, width, height);
        } else {
            const offsets = [[0,0], [boldness,0], [0,boldness], [boldness,boldness], [-boldness,0], [0,-boldness]];
            this.ctx.globalAlpha = 0.5;
            for (const [ox, oy] of offsets) this.ctx.drawImage(img, x + ox, y + oy, width, height);
            this.ctx.globalAlpha = 1.0;
            this.ctx.drawImage(img, x, y, width, height);
        }
    }
    
    updateCursorPosition() {
        if (this.cursorIndex <= 0) {
            this.cursorPos.x = this.config.marginLeft;
            this.cursorPos.y = this.config.marginTop;
        } else {
            // İmleç, cursorIndex-1. karakterin sağındadır
            const targetIndex = this.cursorIndex - 1;
            if (targetIndex < this.charPositions.length) {
                const pos = this.charPositions[targetIndex];
                
                // Eğer önceki karakter '\n' ise (width=0)
                if (pos.width === 0) {
                     this.cursorPos.x = this.config.marginLeft;
                     this.cursorPos.y = pos.y + this.config.lineHeight + this.config.letterScale; // Tahmini
                } else {
                    this.cursorPos.x = pos.x + pos.width + 2; 
                    this.cursorPos.y = pos.y + (pos.height - this.config.letterScale);
                }
            } else {
                this.cursorPos.x = this.config.marginLeft;
                this.cursorPos.y = this.config.marginTop;
            }
        }
    }
    
    drawCursor() {
        if (!this.cursorVisible) return;
        this.ctx.strokeStyle = '#3498db';
        this.ctx.lineWidth = 4;
        const h = this.config.letterScale;
        this.ctx.beginPath();
        this.ctx.moveTo(this.cursorPos.x, this.cursorPos.y);
        this.ctx.lineTo(this.cursorPos.x, this.cursorPos.y + h);
        this.ctx.stroke();
    }
    
    startCursorBlink() {
        if (this.cursorInterval) clearInterval(this.cursorInterval);
        this.cursorInterval = setInterval(() => {
            this.cursorVisible = !this.cursorVisible;
            this.render();
        }, 500);
    }
    
    setMarginTop(v) { this.config.marginTop = v; this.render(); }
    setLineHeight(v) { this.config.lineHeight = v; this.render(); }
    setLetterScale(v) { this.config.letterScale = v; this.render(); }
    setLetterSpacing(v) { this.config.letterSpacing = v; this.render(); }
    setBaselineOffset(v) { this.config.baselineOffset = v; this.render(); }
    setWordSpacing(v) { this.config.wordSpacing = v; this.render(); }
    setBoldness(v) { this.config.boldness = v; this.render(); }
    setJitter(v) { this.config.jitter = v; this.render(); }
    setLineSlope(v) { this.config.lineSlope = v; this.render(); }
    setPaperType(v) { this.config.paperType = v; this.render(); }
    
    setInkColor(v) { 
        this.config.inkColor = v; 
        let filter = 'none';
        if (v === 'bic_mavi') filter = 'sepia(1) hue-rotate(190deg) saturate(3) brightness(0.5)';
        else if (v === 'pilot_mavi') filter = 'sepia(1) hue-rotate(195deg) saturate(4) brightness(0.7)';
        else if (v === 'eski_murekkep') filter = 'sepia(0.5) hue-rotate(185deg) saturate(1.5) brightness(0.6)';
        else if (v === 'kursun') filter = 'grayscale(1) opacity(0.8)';
        else if (v === 'kirmizi') filter = 'sepia(1) hue-rotate(320deg) saturate(5) brightness(0.8)';
        this.canvas.style.filter = filter;
        this.render(); 
    }

    setSpecificLineSlope(index, value) { this.customLineSlopes[index] = value; this.render(); }
    getSpecificLineSlope(index) { return this.customLineSlopes[index]; }
    setShowLines(v) { this.config.showLines = v; this.render(); }
    
    clear() {
        this.textContent = "";
        this.cursorIndex = 0;
        this.render();
    }
    
    exportPNG() {
        const dataURL = this.canvas.toDataURL('image/png', 1.0);
        const a = document.createElement('a');
        a.href = dataURL;
        a.download = `el_yazisi_${Date.now()}.png`;
        a.click();
    }
    
    async exportPDF() {
        const urlParams = new URLSearchParams(window.location.search);
        const fontId = urlParams.get('font_id') || '';
        const userId = urlParams.get('user_id') || '';

        const form = document.createElement('form');
        form.method = 'POST';
        form.action = '/download';
        form.style.display = 'none';
        const params = {
            metin: this.textContent,
            font_id: fontId,
            user_id: userId,
            yazi_boyutu: this.config.letterScale,
            satir_araligi: this.config.lineHeight,
            kelime_boslugu: this.config.wordSpacing,
            kalinlik: this.config.boldness,
            jitter: this.config.jitter,
            line_slope: this.config.lineSlope,
            paper_type: this.config.paperType,
            murekkep_rengi: this.config.inkColor,
            custom_line_slopes: JSON.stringify(this.customLineSlopes)
        };
        for (const [key, value] of Object.entries(params)) {
            const input = document.createElement('input');
            input.type = 'hidden';
            input.name = key;
            input.value = value;
            form.appendChild(input);
        }
        document.body.appendChild(form);
        form.submit();
        setTimeout(() => { document.body.removeChild(form); }, 1000);
    }
}

window.FinalHandwritingEditor = FinalHandwritingEditor;