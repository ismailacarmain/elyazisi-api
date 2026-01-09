# Slim yerine tam sürüm kullanıyoruz (Firebase/GRPC derleme hatalarını önler)
FROM python:3.9

WORKDIR /app

# Sistem kütüphanelerini güncelle (OpenCV için gerekli)
RUN apt-get update && apt-get install -y \
    libgl1-mesa-glx \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Pip güncelle ve paketleri kur
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY . .

# Gunicorn ile başlat
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "8", "--timeout", "0", "app:app"]

