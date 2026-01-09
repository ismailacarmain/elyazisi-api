FROM python:3.11-slim

# Gerekli sistem kütüphaneleri (OpenCV ve PDF için)
RUN apt-get update && apt-get install -y \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgl1 \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Render'ın verdiği PORT'u dinle
CMD gunicorn --bind 0.0.0.0:$PORT app:app