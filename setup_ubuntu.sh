#!/usr/bin/env bash
# Jednorazowa instalacja na Ubuntu: Python, Tesseract (OCR skanów) i zależności.
set -euo pipefail
cd "$(dirname "$0")"
sudo apt-get update -y
sudo apt-get install -y python3 python3-venv python3-pip tesseract-ocr
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
mkdir -p input output
chmod +x run.sh
echo "Gotowe. Uruchomienie: ./run.sh"
