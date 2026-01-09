FROM python:3.11-slim

# Sistem kütüphanelerini kur (OpenCV ve PDF için kritik)
# --no-install-recommends: İmaj boyutunu küçültür ve gereksiz paketleri önler
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgl1 \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .

# Paketleri kur
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Uygulamayı başlat
CMD gunicorn --bind 0.0.0.0:$PORT app:app
