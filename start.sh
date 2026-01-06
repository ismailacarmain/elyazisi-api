#!/bin/bash
set -e

echo "======================================"
echo "🚀 Fontify API Startup Script"
echo "======================================"

# Check Python version
echo "📦 Python version:"
python --version

# Create necessary directories
echo ""
echo "📁 Creating directories..."
mkdir -p static/harfler
mkdir -p templates
mkdir -p temp
echo "  ✓ Directories created"

# Check if we're in production (Render)
if [ -n "$RENDER" ]; then
    echo ""
    echo "🌐 Running on Render.com"
    echo "  Environment: Production"
    echo "  PORT: $PORT"
fi

# Check Firebase credentials
echo ""
echo "🔥 Checking Firebase credentials..."
if [ -n "$FIREBASE_CREDENTIALS" ]; then
    echo "  ✓ Firebase credentials found"
else
    echo "  ⚠ Firebase credentials not found (optional)"
fi

echo ""
echo "======================================"
echo "✅ Startup checks complete!"
echo "======================================"
echo ""
echo "🌐 Starting Gunicorn..."

# Start Gunicorn
exec gunicorn \
    --bind 0.0.0.0:$PORT \
    --workers 2 \
    --threads 4 \
    --timeout 120 \
    --keep-alive 5 \
    --access-logfile - \
    --error-logfile - \
    --log-level info \
    --worker-class sync \
    app:app
