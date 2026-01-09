FROM python:3.10-slim

# Sistem güncellemelerini yap ve sadece gerekli olan poppler'ı kur
RUN apt-get update && \
    apt-get install -y --no-install-recommends poppler-utils && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Bağımlılıkları kopyala ve kur
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Proje dosyalarını kopyala
COPY . .

# Portu ayarla (Render için $PORT çevresel değişkenini kullanır)
ENV PORT=10000
EXPOSE 10000

# Gunicorn ile başlat
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT} --workers 1 --threads 8 --timeout 0 app:app"]