/**
 * EL YAZISI EDİTÖRÜ - FİNAL MOTOR
 * 
 * ÖZELLİKLER:
 * ✅ 2480x3508 (A4 300 DPI) yüksek çözünürlük
 * ✅ Kalınlık algoritması (offset çizim)
 * ✅ State-based (Word mantığı)
 * ✅ Hit testing (mouse tıklama)
 * ✅ Space fix (siyah kutu yok)
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
        
        // CONFIG (Tüm değerler yüksek çözünürlüğe göre)
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
            paperType: 'cizgili', // cizgili, kareli, duz
            inkColor: 'tukenmez', // tukenmez, bic_mavi, kursun
            showLines: true
        };
        
        // STATE
        this.textContent = "";
        this.cursorIndex = 0;
        this.customLineSlopes = {}; // Satır bazlı özel eğimler { index: value }
        
        // UNDO
        this.history = [""];
        this.historyIndex = 0;
        
        // ASSETS
        this.assets = {};
        this.assetsLoaded = false;
        
        // CURSOR
        this.cursorVisible = true;
        this.cursorInterval = null;
        this.cursorPos = { x: this.config.marginLeft, y: this.config.marginTop };
        
        // HIT TESTING
        this.charPositions = [];
        
        this.setupKeyboard();
        this.setupMouse();
    }
    
    async loadAssets(progressCallback) {
        try {
            // URL'den font_id ve user_id bilgilerini al
            const urlParams = new URLSearchParams(window.location.search);
            const fontId = urlParams.get('font_id') || '';
            const userId = urlParams.get('user_id') || '';
            
            const apiUrl = `/api/get_assets?font_id=${fontId}&user_id=${userId}`;
            const response = await fetch(apiUrl);
            const data = await response.json();
            if (!data.success) throw new Error('Asset yüklenemedi');
            
            const assetMap = data.assets;
            const source = data.source; // 'firebase' veya 'local'
            const allKeys = Object.keys(assetMap);
            const total = allKeys.length;
            let loaded = 0;
            
            for (const [key, files] of Object.entries(assetMap)) {
                this.assets[key] = [];
                for (const fileData of files) {
                    try {
                        let src = "";
                        if (source === 'firebase') {
                            // Firebase'den direkt base64 geliyorsa
                            src = fileData.startsWith('data:image') ? fileData : `data:image/png;base64,${fileData}`;
                        } else {
                            // Yerel klasörden dosya adı geliyorsa
                            src = `/static/harfler/${fileData}`;
                        }
                        
                        const img = await this.loadImage(src);
                        this.assets[key].push(img);
                    } catch (err) {
                        console.error(`Hata: ${key} yüklenemedi`);
                    }
                }
                loaded++;
                if (progressCallback) progressCallback(loaded / total);
            }
            
            this.assetsLoaded = true;
            this.startCursorBlink();
            this.render();
            console.log(`✅ ${loaded} harf grubu yüklendi (${source})`);
            return true;
        } catch (error) {
            console.error('Yükleme hatası:', error);
            this.assetsLoaded = true; // Hata olsa da sistemi aç
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
    
    setupKeyboard() {
        document.addEventListener('keydown', (e) => {
            if (!this.assetsLoaded) return;
            
            if (e.ctrlKey && e.key === 'a') {
                e.preventDefault();
                this.textContent = "";
                this.cursorIndex = 0;
                this.addToHistory();
                this.render();
            } else if (e.ctrlKey && e.key === 'z') {
                e.preventDefault();
                this.undo();
            } else if (e.key === 'Backspace') {
                e.preventDefault();
                if (this.cursorIndex > 0) {
                    this.textContent = 
                        this.textContent.slice(0, this.cursorIndex - 1) + 
                        this.textContent.slice(this.cursorIndex);
                    this.cursorIndex--;
                    this.addToHistory();
                    this.render();
                }
            } else if (e.key === 'Delete') {
                e.preventDefault();
                if (this.cursorIndex < this.textContent.length) {
                    this.textContent = 
                        this.textContent.slice(0, this.cursorIndex) + 
                        this.textContent.slice(this.cursorIndex + 1);
                    this.addToHistory();
                    this.render();
                }
            } else if (e.key === 'Enter') {
                e.preventDefault();
                this.insertChar('\n');
            } else if (e.key === 'ArrowLeft') {
                e.preventDefault();
                if (this.cursorIndex > 0) {
                    this.cursorIndex--;
                    this.render();
                }
            } else if (e.key === 'ArrowRight') {
                e.preventDefault();
                if (this.cursorIndex < this.textContent.length) {
                    this.cursorIndex++;
                    this.render();
                }
            } else if (e.key === 'Home') {
                e.preventDefault();
                this.cursorIndex = 0;
                this.render();
            } else if (e.key === 'End') {
                e.preventDefault();
                this.cursorIndex = this.textContent.length;
                this.render();
            } else if (e.key.length === 1 && !e.ctrlKey && !e.altKey) {
                e.preventDefault();
                this.insertChar(e.key);
            }
        });
        
        this.canvas.setAttribute('tabindex', '0');
        this.canvas.focus();
    }
    
    setupMouse() {
        this.canvas.addEventListener('click', (e) => {
            if (!this.assetsLoaded) return;
            
            const rect = this.canvas.getBoundingClientRect();
            // Canvas ekranda küçük ama içi 2480x3508
            const scaleX = this.WIDTH / rect.width;
            const scaleY = this.HEIGHT / rect.height;
            const x = (e.clientX - rect.left) * scaleX;
            const y = (e.clientY - rect.top) * scaleY;
            
            // Hit testing
            let closest = this.textContent.length;
            let minDist = Infinity;
            
            for (let i = 0; i < this.charPositions.length; i++) {
                const pos = this.charPositions[i];
                const dx = x - (pos.x + pos.width / 2);
                const dy = y - (pos.y + pos.height / 2);
                const dist = Math.sqrt(dx * dx + dy * dy);
                
                if (dist < minDist) {
                    minDist = dist;
                    closest = x < pos.x + pos.width / 2 ? i : i + 1;
                }
            }
            
            this.cursorIndex = closest;
            this.canvas.focus();
            this.render();
        });
    }
    
    insertChar(char) {
        this.textContent = 
            this.textContent.slice(0, this.cursorIndex) + 
            char + 
            this.textContent.slice(this.cursorIndex);
        this.cursorIndex += char.length;
        this.addToHistory();
        this.render();
    }
    
    addToHistory() {
        this.history = this.history.slice(0, this.historyIndex + 1);
        this.history.push(this.textContent);
        if (this.history.length > 50) this.history.shift();
        else this.historyIndex++;
    }
    
    undo() {
        if (this.historyIndex > 0) {
            this.historyIndex--;
            this.textContent = this.history[this.historyIndex];
            this.cursorIndex = Math.min(this.cursorIndex, this.textContent.length);
            this.render();
        }
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
    
    /**
     * RENDER - FULL REDRAW
     */
    render() {
        // Temizle
        this.ctx.clearRect(0, 0, this.WIDTH, this.HEIGHT);
        this.ctx.fillStyle = '#ffffff';
        this.ctx.fillRect(0, 0, this.WIDTH, this.HEIGHT);
        
        // Çizgiler / Arka Plan
        this.drawBackground();
        
        // Metin
        this.drawText();
        
        // İmleç
        this.drawCursor();
    }
    
    /**
     * DİNAMİK ARKA PLAN (Kareli/Çizgili)
     */
    drawBackground() {
        if (!this.config.showLines || this.config.paperType === 'duz') return;

        this.ctx.strokeStyle = 'rgba(135, 206, 250, 0.4)'; // Hafif mavi
        this.ctx.lineWidth = 2;
        
        // YATAY ÇİZGİLER (Hem çizgili hem kareli için)
        let y = this.config.marginTop;
        while (y < this.HEIGHT - 100) {
            this.ctx.beginPath();
            this.ctx.moveTo(0, y);
            this.ctx.lineTo(this.WIDTH, y);
            this.ctx.stroke();
            y += this.config.lineHeight;
        }

        // DİKEY ÇİZGİLER (Sadece kareli için)
        if (this.config.paperType === 'kareli') {
            let x = this.config.marginLeft;
            // Grid boyutu satır aralığına eşit olsun
            const gridSize = this.config.lineHeight; 
            
            // Sağa doğru
            while (x < this.WIDTH) {
                this.ctx.beginPath();
                this.ctx.moveTo(x, 0);
                this.ctx.lineTo(x, this.HEIGHT);
                this.ctx.stroke();
                x += gridSize;
            }
            // Sola doğru
            x = this.config.marginLeft;
            while (x > 0) {
                x -= gridSize;
                this.ctx.beginPath();
                this.ctx.moveTo(x, 0);
                this.ctx.lineTo(x, this.HEIGHT);
                this.ctx.stroke();
            }
        }
    }
    
    /**
     * METİN ÇİZ - WORD-WRAP + KALINLIK
     */
    drawText() {
        this.charPositions = [];
        let x = this.config.marginLeft;
        let y = this.config.marginTop;
        const maxX = this.WIDTH - this.config.marginRight;
        
        // SATIR BAZLI DEĞİŞKENLER
        let lineIndex = 0;
        // Eğim çarpanını (Slope) yeni parametreye bağladık
        const getLineSlope = (idx) => {
            // 1. Öncelik: Kullanıcının özel ayarı
            if (this.customLineSlopes[idx] !== undefined) {
                return this.customLineSlopes[idx] * 0.0015;
            }
            // 2. Öncelik: Global rastgele eğim
            return Math.sin(idx * 123.456) * (this.config.lineSlope * 0.0015);
        };
        const getLineYOffset = (idx) => Math.cos(idx * 543.21) * (this.config.lineSlope * 1.5);   
        
        for (let i = 0; i < this.textContent.length; i++) {
            const char = this.textContent[i];
            
            // Yeni satır kontrolü (Slope ve Offset için)
            if (char === '\n' || x === this.config.marginLeft) {
                // Her yeni satır başlangıcında lineIndex artar (wrap veya \n)
                if (char === '\n') {
                    this.charPositions.push({ x, y: y - this.config.letterScale, width: 0, height: this.config.letterScale });
                    x = this.config.marginLeft;
                    y += this.config.lineHeight;
                    lineIndex++;
                    continue;
                }
            }
            
            // Space
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
            
            // Normal karakter
            const img = this.getLetterImage(char);
            if (!img) continue;
            
            // LAYOUT HESAPLAMA (Sabit, Jitter'dan etkilenmez)
            // Bu sayede ayarları değiştirince yazı akışı bozulmaz
            const layoutScale = this.config.letterScale / img.height;
            const layoutWidth = img.width * layoutScale;
            const layoutHeight = this.config.letterScale;

            // Word-wrap (Sabit genişliğe göre)
            if (x + layoutWidth > maxX) {
                x = this.config.marginLeft;
                y += this.config.lineHeight;
                lineIndex++;
            }
            
            // GÖRSEL HESAPLAMA (Jitter burada devreye girer)
            const chaosLevel = this.config.jitter;
            
            // --- AKILLI ÖLÇEKLENDİRME (SMART SCALING) ---
            let typeScale = 1.0;
            let typeYOffset = 0; // Aşağı/Yukarı kaydırma

            if (/[A-ZÇĞİÖŞÜ]/.test(char)) {
                // Büyük Harfler: Tam Boy (Referans)
                typeScale = 1.0; 
            } else if (/[bdfhklt]/.test(char)) {
                // Yukarı Uzayan Küçükler (Ascenders): %95 Boy
                typeScale = 0.95;
            } else if (/[gjpqy]/.test(char)) {
                // Aşağı Sarkan Küçükler (Descenders): %90 Boy + Aşağı Kayma
                typeScale = 0.90;
                typeYOffset = this.config.letterScale * 0.25; 
            } else if (/[acemnorsuvwxzıüğişöç]/.test(char)) {
                // Orta Boy Küçükler (x-height): %65 Boy + Hafif Aşağı (Ortalama)
                typeScale = 0.65;
                typeYOffset = this.config.letterScale * 0.15;
            } else if (/[.,:;]/.test(char)) {
                // Noktalama (Alt): Çok küçük + Tabana yapışık
                typeScale = 0.25;
                typeYOffset = this.config.letterScale * 0.40;
            } else if (/['"`^]/.test(char)) {
                // Noktalama (Üst): Küçük + Tavana yakın
                typeScale = 0.25;
                typeYOffset = -this.config.letterScale * 0.35;
            } else if (/[-+*=/]/.test(char)) {
                // Matematiksel / Orta
                typeScale = 0.50;
                typeYOffset = this.config.letterScale * 0.10;
            }

            // 1. MİKRO BOYUT VARYASYONU (TypeScale ile çarpılır)
            const sizeNoise = (Math.sin(i * 4321) * 0.01 * chaosLevel); 
            const drawScale = this.config.letterScale * typeScale * (1 + sizeNoise);
            
            const drawImgScale = drawScale / img.height;
            const drawWidth = img.width * drawImgScale;
            const drawHeight = drawScale;
            
            // 2. DOĞAL SATIR EĞİMİ VE YAMUKLUĞU
            const slope = getLineSlope(lineIndex);
            const lineOffset = getLineYOffset(lineIndex);
            const relativeX = x - this.config.marginLeft;
            const slopeY = relativeX * slope;
            
            // 3. MİKRO ROTASYON
            const angle = (Math.sin(i * 987.65) * 0.003 * chaosLevel);
            
            // 4. HARF BAZLI TİTREME
            const jitterY = Math.cos(i * 135.79) * (chaosLevel * 0.4);
            
            // Çizim Y koordinatı (Layout Y + Efektler + TypeOffset)
            // drawHeight ile layoutHeight farkını ortalamak yerine, TypeOffset ile manuel konumlandırıyoruz.
            // Taban çizgisine (baseline) göre hizalama mantığı:
            
            // Temel Y: Satırın alt çizgisine yakın bir yer
            const baseY = y + this.config.baselineOffset + slopeY + lineOffset + jitterY;
            
            // Harfin çizileceği Y: BaseY - HarfBoyu + ÖzelKaydırma
            // (Bu formül harfleri alt çizgiye hizalar, küçükleri ortalar, sarkanları aşağı salar)
            const drawY = baseY - drawHeight + typeYOffset;
            
            this.ctx.save();
            // Dönme merkezi (Harfin ortası)
            const centerX = x + layoutWidth / 2;
            const centerY = drawY + drawHeight / 2;
            
            this.ctx.translate(centerX, centerY);
            this.ctx.rotate(angle);
            this.ctx.translate(-centerX, -centerY);
            
            // Resmi çiz (Jitter'lı boyutlarla, ama layout merkezli)
            // X koordinatını ortala
            const drawX = x + (layoutWidth - drawWidth) / 2;
            
            this.drawBoldImage(img, drawX, drawY, drawWidth, drawHeight);
            this.ctx.restore();
            
            this.charPositions.push({ x, y: drawY, width: layoutWidth, height: layoutHeight });
            
            // X'i sabit layout genişliği kadar ilerlet
            x += layoutWidth + this.config.letterSpacing;
        }
        
        // Toplam görsel satır sayısını kaydet
        this.visualLineCount = lineIndex + 1;
        
        // İmleç pozisyonu hesapla
        this.updateCursorPosition();
    }

    getVisualLineCount() {
        return this.visualLineCount || 1;
    }
    
    /**
     * KALINLIK ALGORİTMASI - OFFSET ÇİZİM
     */
    drawBoldImage(img, x, y, width, height) {
        const boldness = this.config.boldness;
        
        if (boldness === 0) {
            // Normal çizim
            this.ctx.drawImage(img, x, y, width, height);
        } else {
            // Kalınlık: Offset çizim
            const offsets = [
                [0, 0],                    // Merkez
                [boldness, 0],             // Sağ
                [0, boldness],             // Alt
                [boldness, boldness],      // Sağ-alt
                [-boldness, 0],            // Sol
                [0, -boldness],            // Üst
            ];
            
            // Hafif opacity ile üst üste çiz
            this.ctx.globalAlpha = 0.5;
            for (const [ox, oy] of offsets) {
                this.ctx.drawImage(img, x + ox, y + oy, width, height);
            }
            this.ctx.globalAlpha = 1.0;
            
            // Son olarak merkeze net çiz
            this.ctx.drawImage(img, x, y, width, height);
        }
    }
    
    /**
     * İMLEÇ POZİSYONU GÜNCELLE
     */
    updateCursorPosition() {
        if (this.cursorIndex === 0) {
            this.cursorPos.x = this.config.marginLeft;
            this.cursorPos.y = this.config.marginTop;
        } else if (this.cursorIndex <= this.charPositions.length) {
            const prevPos = this.charPositions[this.cursorIndex - 1];
            if (!prevPos) {
                this.cursorPos.x = this.config.marginLeft;
                this.cursorPos.y = this.config.marginTop;
            } else if (prevPos.width === 0) { // \n
                this.cursorPos.x = this.config.marginLeft;
                this.cursorPos.y = prevPos.y + this.config.lineHeight + this.config.letterScale;
            } else {
                this.cursorPos.x = prevPos.x + prevPos.width;
                this.cursorPos.y = prevPos.y;
            }
        }
    }
    
    /**
     * İMLEÇ ÇİZ
     */
    drawCursor() {
        if (!this.cursorVisible) return;
        
        this.ctx.strokeStyle = '#3498db';
        this.ctx.lineWidth = 4;
        this.ctx.beginPath();
        this.ctx.moveTo(this.cursorPos.x, this.cursorPos.y);
        this.ctx.lineTo(this.cursorPos.x, this.cursorPos.y + this.config.letterScale);
        this.ctx.stroke();
    }
    
    startCursorBlink() {
        if (this.cursorInterval) clearInterval(this.cursorInterval);
        this.cursorInterval = setInterval(() => {
            this.cursorVisible = !this.cursorVisible;
            this.render();
        }, 500);
    }
    
    /**
     * AYARLAR
     */
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

    setSpecificLineSlope(index, value) {
        this.customLineSlopes[index] = value;
        this.render();
    }

    getSpecificLineSlope(index) {
        return this.customLineSlopes[index];
    }
    
    setShowLines(v) { this.config.showLines = v; this.render(); }
    
    clear() {
        this.textContent = "";
        this.cursorIndex = 0;
        this.history = [""];
        this.historyIndex = 0;
        this.render();
    }
    
    setText(text) {
        this.textContent = text;
        this.cursorIndex = text.length;
        this.addToHistory();
        this.render();
    }
    
    getStats() {
        const lines = (this.textContent.match(/\n/g) || []).length + 1;
        const words = this.textContent.split(/\s+/).filter(w => w.length > 0).length;
        return { chars: this.textContent.length, words, lines };
    }
    
    /**
     * EXPORT PNG (Yüksek çözünürlük)
     */
    exportPNG() {
        const dataURL = this.canvas.toDataURL('image/png', 1.0);
        const a = document.createElement('a');
        a.href = dataURL;
        a.download = `el_yazisi_${Date.now()}.png`;
        a.click();
    }
    
    /**
     * EXPORT PDF - SERVER SIDE RENDER
     * Tüm ayarları backend'e gönderir
     */
    async exportPDF() {
        // Form oluştur
        const form = document.createElement('form');
        form.method = 'POST';
        form.action = '/download';
        form.style.display = 'none';

        // Parametreleri ekle
        const params = {
            metin: this.textContent,
            yazi_boyutu: this.config.letterScale,
            satir_araligi: this.config.lineHeight,
            kelime_boslugu: this.config.wordSpacing,
            kalinlik: this.config.boldness,
            jitter: this.config.jitter,
            line_slope: this.config.lineSlope,
            paper_type: this.config.paperType,  // Yeni: Kağıt tipi
            murekkep_rengi: this.config.inkColor, // Yeni: Mürekkep rengi
            custom_line_slopes: JSON.stringify(this.customLineSlopes) // YENİ: Satır bazlı özel eğimler
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
        
        // Temizle
        setTimeout(() => {
            document.body.removeChild(form);
        }, 1000);
    }
}

window.FinalHandwritingEditor = FinalHandwritingEditor;
