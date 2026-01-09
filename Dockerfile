FROM python:3.9-slim

WORKDIR /app

# Sistem bağımlılıklarını ve derleme araçlarını kur
# build-essential ve python3-dev: Derleme gerektiren paketler için şarttır.
RUN apt-get update && apt-get install -y \
    build-essential \
    python3-dev \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgl1 \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Pip'i güncelle ve bağımlılıkları kur
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "8", "--timeout", "0", "app:app"]