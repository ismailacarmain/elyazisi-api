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

## AI sağlayıcı yedekleme

Fontify AI Studio tek bir sağlayıcıya bağlı değildir. Render environment
variables üzerinden aşağıdaki anahtarlar yapılandırılabilir:

- `GEMINI_API_KEY`: Ana belge planlama sağlayıcısı.
- `GROQ_API_KEY`: Ücretsiz kotası yüksek, structured JSON destekli Copilot sağlayıcısı.
- `OPENROUTER_API_KEY`: Son yedek sağlayıcı; varsayılan model `openrouter/free`.
- `GROQ_MODEL`: Varsayılan `openai/gpt-oss-120b`.
- `OPENROUTER_MODEL`: Varsayılan `openrouter/free`.
- `AI_DOCUMENT_PROVIDER_ORDER`: Varsayılan `gemini,groq,openrouter`.
- `COPILOT_PROVIDER_ORDER`: Varsayılan `groq,gemini,openrouter`.

Anahtarlar kaynak koda veya frontend'e yazılmaz. Sağlayıcılar sırayla denenir;
kota, bağlantı veya servis hatasında bir sonraki sağlayıcıya geçilir.
