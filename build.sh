#!/usr/bin/env bash
# Script de build para Render.
# Se ejecuta una sola vez durante el despliegue para instalar
# dependencias de Python y el navegador Chromium de Playwright.

set -e  # Detiene el script si cualquier comando falla

echo "→ Instalando dependencias de Python..."
pip install -r requirements.txt

echo "→ Instalando Chromium para Playwright..."
playwright install chromium

echo "→ Instalando dependencias del sistema para Chromium..."
playwright install-deps chromium

echo "✓ Build completado"
