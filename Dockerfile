FROM python:3.10-slim

WORKDIR /app

# Sadece PDF için gerekli olan poppler'ı kur (En sade hali)
RUN apt-get update && \
    apt-get install -y --no-install-recommends poppler-utils && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Bağımlılıkları kur
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Tüm dosyaları kopyala
COPY . .

# Port ayarı
ENV PORT=10000
EXPOSE 10000

# Uygulamayı başlat
CMD ["gunicorn", "--bind", "0.0.0.0:10000", "--workers", "1", "--threads", "8", "--timeout", "0", "app:app"]
