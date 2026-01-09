# Tam sürüm Python kullanıyoruz (Hata riskini sıfırlar)
FROM python:3.10

WORKDIR /app

# Sistem kütüphanelerini güncelle
# libgl1: OpenCV için şart
# poppler-utils: PDF işlemleri için şart
RUN apt-get update && apt-get install -y \
    libgl1 \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Paketleri kur
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY . .

# Uygulamayı başlat
CMD gunicorn --bind 0.0.0.0:$PORT app:app --timeout 120 --workers 1 --threads 8