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

# Startup script'e çalıştırma izni ver
RUN chmod +x start.sh

# Gerekli klasörleri oluştur (hata önleme)
RUN mkdir -p static/harfler templates temp

# Port
ENV PORT=8080

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health', timeout=5)"

# Startup script ile başlat
CMD ["./start.sh"]
