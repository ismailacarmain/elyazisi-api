FROM python:3.11-slim

# OpenCV için gerekli sistem bağımlılıkları
RUN apt-get update && apt-get install -y \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Requirements'ı kopyala ve yükle
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Tüm dosyaları kopyala
COPY . .

# Static klasörü oluştur
RUN mkdir -p static/harfler templates

# Port
ENV PORT=8080

# Gunicorn ile başlat
CMD gunicorn --bind 0.0.0.0:$PORT --workers 2 --timeout 120 app:app
