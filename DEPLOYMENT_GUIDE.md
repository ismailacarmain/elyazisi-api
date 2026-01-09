# 🚀 DEPLOYMENT REHBERİ - BACKEND

**Hazırlayan:** Claude (Anthropic)  
**Tarih:** 9 Ocak 2026  
**Durum:** ✅ Production Ready

---

## 📦 DOSYALAR

1. **app.py** - Ana backend dosyası (TAM VE EKSİKSİZ!)
2. **requirements.txt** - Python dependencies
3. **render.yaml** - Render konfigürasyonu
4. **.gitignore** - Git ignore kuralları
5. **Dockerfile** - Docker konfigürasyonu

---

## ✅ YENİ ÖZELLİKLER

### 1. Token/Credit Sistemi DÜZELTİLDİ! ✨

**Sorun:** Decorator sırası yanlıştı  
**Çözüm:** 
```python
@app.route('/api/upload_form')
@login_required  # 1. Önce auth (request.uid set edilir)
@check_credits   # 2. Sonra credit (request.uid kullanır)
def upload_form():
```

### 2. Detaylı Error Handling

```python
# Token expired
{'error': 'TOKEN_EXPIRED', 'message': 'Oturumunuz sona erdi'}

# Credit yetersiz  
{'error': 'INSUFFICIENT_CREDITS', 'current_credits': 0, 'required': 1}

# Rate limit
{'message': 'Çok hızlı istek. 5 saniye bekleyin'}
```

### 3. Rate Limiting

- 10 saniyede bir upload
- User bazlı kontrol

### 4. CORS Düzeltildi

```python
# Sadece fontify.online allowed
origins: ["https://fontify.online", "https://www.fontify.online"]
```

### 5. Comprehensive Logging

- Auth events
- Credit usage
- Errors
- Security events

---

## 🔧 KURULUM

### 1. GitHub Repository Oluştur

```bash
# Local'de
mkdir elyazisi-api
cd elyazisi-api

# Dosyaları kopyala
cp /path/to/app.py .
cp /path/to/requirements.txt .
cp /path/to/Dockerfile .
cp /path/to/render.yaml .
cp /path/to/.gitignore .

# core_generator.py ve diğer modüllerinizi ekleyin
# form_olustur.py, harf_kesici.py vs.

# Git init
git init
git add .
git commit -m "Initial commit - Production ready backend"

# GitHub'a push
git remote add origin https://github.com/USERNAME/elyazisi-api.git
git branch -M main
git push -u origin main
```

---

## 🚀 RENDER DEPLOYMENT

### 1. Render'a Giriş

https://dashboard.render.com/

### 2. New Web Service

**Blueprint → Connect GitHub → Select Repo**

### 3. Settings

```
Name: elyazisi-api
Environment: Docker
Region: Frankfurt
Branch: main
Plan: Free
```

### 4. Environment Variables (ÇOK ÖNEMLİ!)

Render Dashboard → Environment:

```bash
# 1. Flask Environment
FLASK_ENV=production

# 2. Port
PORT=10000

# 3. reCAPTCHA Secret (Google reCAPTCHA admin'den alın)
RECAPTCHA_SECRET_KEY=your_recaptcha_secret_key_here

# 4. Firebase Credentials (JSON formatında, TEK SATIR!)
FIREBASE_CREDENTIALS={"type":"service_account","project_id":"elyazisiapp","private_key_id":"...","private_key":"-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n","client_email":"...","client_id":"...","auth_uri":"https://accounts.google.com/o/oauth2/auth","token_uri":"https://oauth2.googleapis.com/token","auth_provider_x509_cert_url":"https://www.googleapis.com/oauth2/v1/certs","client_x509_cert_url":"..."}
```

**Firebase Credentials Nasıl Alınır:**
1. Firebase Console → Project Settings → Service Accounts
2. "Generate new private key" tıkla
3. JSON dosyasını indir
4. İçeriği TEK SATIRA sıkıştır (newline'ları kaldır)
5. Render'a yapıştır

### 5. Deploy

**"Create Web Service"** → Otomatik deploy başlar

**İlk deploy:** ~5-10 dakika  
**Sonraki deploylar:** ~2-3 dakika

---

## 🧪 TEST

### 1. Health Check

```bash
curl https://elyazisi-api.onrender.com/health
```

**Beklenen:**
```json
{"status": "healthy", "firebase": "connected"}
```

### 2. Credit Endpoint (Token gerekli)

```bash
# Browser Console'da (F12)
const user = firebase.auth().currentUser;
const token = await user.getIdToken();

fetch('https://elyazisi-api.onrender.com/api/user/credits', {
    headers: { 'Authorization': `Bearer ${token}` }
})
.then(r => r.json())
.then(d => console.log('Credits:', d));
```

**Beklenen:**
```json
{"success": true, "credits": 10}
```

### 3. Upload Test

```bash
# Browser Console
const formData = {
    font_name: 'Test Font',
    image: 'data:image/png;base64,...'
};

fetch('https://elyazisi-api.onrender.com/api/upload_form', {
    method: 'POST',
    headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`
    },
    body: JSON.stringify(formData)
})
.then(r => r.json())
.then(d => console.log('Result:', d));
```

---

## 🐛 TROUBLESHOOTING

### Hata: "Firebase not connected"

**Sebep:** FIREBASE_CREDENTIALS yanlış veya eksik  
**Çözüm:** Environment variable'ı kontrol edin, JSON formatı doğru mu?

### Hata: "Missing Authorization header"

**Sebep:** Frontend token göndermiyor  
**Çözüm:** ekle.html veya engine.js'de Authorization header ekleyin

### Hata: "TOKEN_EXPIRED"

**Sebep:** Token süresi dolmuş  
**Çözüm:** Frontend'de `getIdToken(true)` ile refresh edin

### Hata: "INSUFFICIENT_CREDITS"

**Sebep:** Credit gerçekten yok  
**Çözüm:** Firestore → users collection → user document → credits field kontrol

### Hata: 500 Internal Server Error

**Sebep:** core_generator.py veya diğer modüller eksik  
**Çözüm:** Tüm modüllerin GitHub'da olduğundan emin olun

---

## 📊 FIRESTORE STRUCTURE

### Users Collection

```javascript
users/{uid}/
  - credits: 10 (number)
  - email: "user@example.com" (string)
  - created_at: timestamp
  - last_used: timestamp
```

### Fonts Collection

```javascript
fonts/{font_id}/
  - name: "My Font" (string)
  - owner_id: "user_uid" (string)
  - owner_email: "user@example.com" (string)
  - is_public: false (boolean)
  - created_at: timestamp
  - characters: {...} (map)
  - download_url: "https://..." (string)
```

---

## 🔐 GÜVENLİK

### Aktif Korumaları

1. ✅ **CORS:** Sadece fontify.online
2. ✅ **HTTPS:** Zorunlu (production)
3. ✅ **Auth:** Firebase token validation
4. ✅ **Credit System:** User-based rate limiting
5. ✅ **Input Validation:** XSS, path traversal koruması
6. ✅ **SSRF Protection:** URL whitelist
7. ✅ **Rate Limiting:** 10 sn/istek
8. ✅ **Security Headers:** CSP, HSTS, X-Frame-Options
9. ✅ **Logging:** Tüm events loglanıyor

---

## 📈 MONITORING

### Render Dashboard

- **Logs:** Real-time log stream
- **Metrics:** CPU, Memory, Response time
- **Deploys:** Deploy history

### Firestore

- **Usage:** Firestore Console → Usage
- **Requests:** Read/Write statistics

---

## 🎯 CHECKLIST

Deployment öncesi:

- [ ] Tüm modüller GitHub'da (core_generator, form_olustur, harf_kesici)
- [ ] Environment variables Render'da set edildi
- [ ] Firebase credentials JSON formatında (tek satır)
- [ ] CORS origins doğru (fontify.online)
- [ ] .gitignore'da serviceAccountKey.json var
- [ ] Dockerfile port 10000
- [ ] requirements.txt güncel

Deployment sonrası:

- [ ] Health check çalışıyor (/health)
- [ ] Firebase connected
- [ ] Credit endpoint çalışıyor
- [ ] Upload endpoint çalışıyor
- [ ] Logs akıyor
- [ ] Frontend bağlanabiliyor

---

## 🚨 ÖNEMLİ NOTLAR

1. **Render Free Tier:** 
   - 15 dakika inactivity sonrası sleep
   - İlk istek 30-60 sn sürebilir (cold start)
   - Aylık 750 saat ücretsiz

2. **Firebase Quotas:**
   - Free tier: 50K reads, 20K writes/gün
   - Aşılırsa ücret veya limit

3. **File Size Limits:**
   - Max request: 50MB
   - Max image: 10MB (validation'da)
   - Dockerfile'da artırılabilir

4. **Secrets:**
   - ASLA Git'e commit etmeyin
   - Sadece Render Environment Variables

---

## 📞 DESTEK

**Hata durumunda:**
1. Render Logs'u kontrol edin
2. Browser Console'u kontrol edin (F12)
3. Firebase Console'da errors var mı bakın

---

**Son Güncelleme:** 9 Ocak 2026  
**Versiyon:** 2.0 (Token/Credit Fix)  
**Durum:** ✅ Production Ready
