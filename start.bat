@echo off
title ReadWorks Agent

echo Instalando dependencias...
pip install -q flask anthropic playwright python-dotenv
playwright install chromium --quiet

echo.
echo ================================
echo   Abre: http://localhost:5000
echo ================================
echo.

python app.py
pause
