FROM python:3.9-slim

WORKDIR /app

# Sistem bağımlılıklarını kur (OpenCV ve PDF için gerekli)
RUN apt-get update && apt-get install -y \
    libglib2.0-0 libsm6 libxext6 libxrender-dev libgl1 poppler-utils \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-10000} --workers 1 --threads 8 --timeout 0 app:app"]
