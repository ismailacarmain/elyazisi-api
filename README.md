# El Yazısı API

El yazısı formlarını işleyen ve harfleri çıkaran API servisi.

## Endpoint'ler

### GET /
API bilgisi

### GET /health
Sunucu durumu kontrolü

### POST /process
İki sayfa JPG gönder, harfleri çıkar ve Firebase'e kaydet.

**Body:**
```json
{
    "user_id": "firebase_user_id",
    "font_name": "El Yazım 1",
    "image1": "base64_encoded_jpg",
    "image2": "base64_encoded_jpg"
}
```

**Response:**
```json
{
    "success": true,
    "font_id": "uuid",
    "font_name": "El Yazım 1",
    "harf_sayisi": 219,
    "message": "219 harf başarıyla işlendi!"
}
```

## Kurulum (Render)

1. GitHub'a push et
2. Render'da yeni Web Service oluştur
3. Environment variables ekle:
   - FIREBASE_PROJECT_ID
   - FIREBASE_PRIVATE_KEY_ID
   - FIREBASE_PRIVATE_KEY
   - FIREBASE_CLIENT_EMAIL
   - FIREBASE_CLIENT_ID
