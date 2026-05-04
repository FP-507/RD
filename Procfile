# Comando de inicio para plataformas compatibles con Heroku/Procfile (Railway, etc.)
web: gunicorn app:app --workers=1 --threads=8 --timeout=300 --bind 0.0.0.0:$PORT
