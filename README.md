# Fontify API - El Yazısı Dijitalleştirme Servisi

## 📋 Genel Bakış

Fontify, kullanıcıların kendi el yazılarını dijitalleştirerek PDF formatında kullanmalarını sağlayan bir web uygulamasıdır.

## 🚀 Özellikler

- ✅ Aruco marker tabanlı otomatik form tanıma
- ✅ El yazısı karakterlerini PNG formatında kaydetme
- ✅ Firebase Firestore entegrasyonu
- ✅ Gerçekçi el yazısı simülasyonu
- ✅ PDF oluşturma ve indirme
- ✅ Çoklu karakter varyasyonu desteği (1, 3, 5, 10)
- ✅ Özelleştirilebilir yazı stilleri

## 📦 Kurulum

### Gereksinimler

- Python 3.11+
- Docker (Render deployment için)
- Firebase hesabı

### Yerel Geliştirme

```bash
# Repoyu klonla
git clone <your-repo-url>
cd fontify-api

# Virtual environment oluştur
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Bağımlılıkları yükle
pip install -r requirements.txt

# Çalıştır
python app.py
```

## 🔧 API Endpoint'leri

### GET `/health`
Sunucu sağlık kontrolü
```
Response: "OK"
```

### GET `/api/generate_form?variation_count=3`
Boş form PDF'i oluşturur
```
Query Params:
  - variation_count: 1, 3, 5, veya 10 (default: 3)

Response: PDF dosyası
```

### GET `/api/generate_example?variation_count=3`
Örnek dolu form PDF'i oluşturur
```
Query Params:
  - variation_count: 1, 3, 5, veya 10 (default: 3)

Response: PDF dosyası
```

### GET `/api/get_assets?font_id=xxx&user_id=yyy`
Firebase'den font assetlerini getirir
```
Query Params:
  - font_id: Font ID
  - user_id: Kullanıcı ID

Response:
{
  "success": true,
  "assets": {...},
  "source": "firebase"
}
```

### POST `/process_single`
Tek sayfa tarama işlemi
```json
{
  "user_id": "firebase_user_id",
  "font_name": "Benim Yazım",
  "image_base64": "base64_encoded_image",
  "section_id": 0,
  "variation_count": 3
}
```

Response:
```json
{
  "success": true,
  "detected_chars": 60,
  "section_id": 0
}
```

### POST `/download`
El yazısı PDF'i oluşturur ve indirir
```
Form Data:
  - font_id: Font ID
  - user_id: User ID
  - metin: Yazılacak metin
  - yazi_boyutu: 140 (default)
  - satir_araligi: 220 (default)
  - kelime_boslugu: 55 (default)
  - jitter: 3 (default)
  - kalinlik: 0 (default)
  - paper_type: 'duz', 'cizgili', 'kareli'

Response: PDF dosyası
```

## 🐳 Render.com Deployment

### 1. GitHub'a Push Et

```bash
git add .
git commit -m "Ready for deployment"
git push origin main
```

### 2. Render'da Proje Oluştur

1. [Render Dashboard](https://dashboard.render.com)'a git
2. "New +" → "Web Service" seç
3. GitHub reponuzu bağlayın
4. Ayarlar:
   - **Name**: fontify-api
   - **Environment**: Docker
   - **Plan**: Free
   - **Branch**: main

### 3. Environment Variables Ekle

Render dashboard'da Environment sekmesinde:

```
FIREBASE_CREDENTIALS
```

Firebase credentials'ı JSON string olarak ekleyin:

```json
{
  "type": "service_account",
  "project_id": "your-project-id",
  "private_key_id": "your-key-id",
  "private_key": "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n",
  "client_email": "your-service-account@your-project.iam.gserviceaccount.com",
  "client_id": "your-client-id",
  "auth_uri": "https://accounts.google.com/o/oauth2/auth",
  "token_uri": "https://oauth2.googleapis.com/token",
  "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
  "client_x509_cert_url": "https://www.googleapis.com/robot/v1/metadata/x509/..."
}
```

### 4. Deploy

"Create Web Service" butonuna tıklayın. Render otomatik olarak:
- Dockerfile'ı build edecek
- Dependencies'leri yükleyecek
- Servisi başlatacak

## 🔍 Troubleshooting

### Deploy Başarısız Oluyor

**Hata**: "Exited with status 3"

**Çözüm**:
1. Logs'u kontrol edin
2. `requirements.txt` versiyonlarını kontrol edin
3. Dockerfile build adımlarını gözden geçirin
4. Firebase credentials'ın doğru olduğundan emin olun

### OpenCV Hatası

**Hata**: "libGL.so.1: cannot open shared object file"

**Çözüm**: Dockerfile'da `libgl1` paketinin yüklü olduğundan emin olun (zaten ekli)

### Import Hatası

**Hata**: "ModuleNotFoundError: No module named 'PIL'"

**Çözüm**: `requirements.txt`'e `Pillow` ekleyin (zaten ekli)

### Timeout Hatası

**Hata**: Worker timeout

**Çözüm**: Dockerfile CMD satırında `--timeout 120` parametresi ekli

## 📝 Karakter Desteği

### Türkçe Karakterler
- Küçük: a-z, ç, ğ, ı, i, ö, ş, ü
- Büyük: A-Z, Ç, Ğ, I, İ, Ö, Ş, Ü

### İngilizce Karakterler
- Küçük: w, q, x
- Büyük: W, Q, X

### Rakamlar
0-9

### Özel Karakterler
```
. , : ; ? ! - ( ) " ' [ ] { } / \ | + * = < > % ^ # ~ _ @ $ € ₺ &
```

## 🎨 Karakter Hizalama Mantığı

```python
# Küçük harfler: %72 ölçek
smalls = "aceimnorsuvwxzçöüşiı-+*=<>%^#~"

# Uzun harfler: %95 ölçek
ascenders = "bdfhklt"

# Kuyruklu harfler: %72 ölçek + %22 aşağı kayma
descenders = "gjpyqğ_"

# Noktalar: %28 ölçek
punctuation = ".,:;'\""

# Uzun noktalama: %90 ölçek
tall_punctuation = "!?()[]{}/\\|@$€₺&"
```

## 🔐 Güvenlik

- Firebase credentials environment variable olarak saklanır
- CORS yapılandırması aktif
- Input validation yapılır
- Error handling uygulanmış

## 📊 Performans

- Gunicorn ile 2 worker
- 120 saniye timeout
- Headless OpenCV (daha hafif)
- Optimize edilmiş görüntü işleme

## 🤝 Katkıda Bulunma

1. Fork edin
2. Feature branch oluşturun (`git checkout -b feature/AmazingFeature`)
3. Commit edin (`git commit -m 'Add some AmazingFeature'`)
4. Push edin (`git push origin feature/AmazingFeature`)
5. Pull Request açın

## 📄 Lisans

Bu proje MIT lisansı altındadır.

## 📞 Destek

Sorularınız için issue açabilirsiniz.

---

**Not**: Firebase credentials'ınızı asla public repository'ye commitlemeyin!
