"""
Bootstrap de inicio automático.

La primera vez que se ejecuta:
  1. Crea un entorno virtual en .venv/
  2. Instala todas las dependencias (flask, anthropic, playwright...)
  3. Descarga Chromium para Playwright

Las veces siguientes arranca directamente en menos de 1 segundo.

Cómo usarlo:
  - VS Code: presionar F5
  - Terminal: python run.py
"""

import os
import subprocess
import sys
import threading
import time
import webbrowser

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VENV_DIR = os.path.join(BASE_DIR, ".venv")

# Archivo que indica que el setup ya fue completado.
# Si no existe, se ejecuta el setup completo.
MARKER = os.path.join(VENV_DIR, ".ready")


# ── Rutas del entorno virtual ──────────────────────────────────────────────

def venv_python() -> str:
    """Ruta al ejecutable de Python dentro del entorno virtual."""
    if sys.platform == "win32":
        return os.path.join(VENV_DIR, "Scripts", "python.exe")
    return os.path.join(VENV_DIR, "bin", "python")


def venv_pip() -> str:
    """Ruta al pip dentro del entorno virtual."""
    if sys.platform == "win32":
        return os.path.join(VENV_DIR, "Scripts", "pip.exe")
    return os.path.join(VENV_DIR, "bin", "pip")


def in_venv() -> bool:
    """Devuelve True si este proceso ya corre dentro del .venv."""
    return os.path.abspath(sys.executable) == os.path.abspath(venv_python())


# ── Setup (solo la primera vez) ────────────────────────────────────────────

def setup() -> None:
    """Crea el venv, instala dependencias Python y descarga Chromium."""
    print("\n📦 Primera vez — configurando el entorno automáticamente...\n")

    # Paso 1: crear entorno virtual
    if not os.path.exists(VENV_DIR):
        print("  [1/3] Creando entorno virtual...")
        subprocess.run(
            [sys.executable, "-m", "venv", VENV_DIR],
            check=True,
        )

    # Paso 2: instalar dependencias de Python
    print("  [2/3] Instalando dependencias (flask, anthropic, playwright...)  ")
    subprocess.run(
        [venv_pip(), "install", "-r", os.path.join(BASE_DIR, "requirements.txt"), "-q"],
        check=True,
    )

    # Paso 3: descargar Chromium para Playwright
    print("  [3/3] Descargando Chromium (puede tardar un momento)...")
    subprocess.run(
        [venv_python(), "-m", "playwright", "install", "chromium"],
        check=True,
    )

    # Marca el setup como completado para no repetirlo
    open(MARKER, "w").close()
    print("\n✅ Configuración completa — iniciando el agente...\n")


# ── Apertura del navegador ─────────────────────────────────────────────────

def open_browser_after(seconds: int = 2) -> None:
    """Abre http://localhost:5000 en el navegador tras una breve espera."""
    def _open():
        time.sleep(seconds)
        webbrowser.open("http://localhost:5000")
    threading.Thread(target=_open, daemon=True).start()


# ── Entry point ────────────────────────────────────────────────────────────

def main() -> None:
    # 1. Ejecutar setup si es la primera vez
    if not os.path.exists(MARKER):
        setup()

    # 2. Si no estamos dentro del venv, re-lanzar con el Python del venv.
    #    Esto garantiza que flask, anthropic, etc. estén disponibles.
    if not in_venv():
        py = venv_python()
        if not os.path.exists(py):
            # El venv existe pero el marcador estaba mal — correr setup de nuevo
            open(MARKER, "w").close()
            os.remove(MARKER)
            setup()
        result = subprocess.run([py] + sys.argv)
        sys.exit(result.returncode)

    # 3. Lanzar la app (ya estamos dentro del venv)
    print("🚀  ReadWorks Agent corriendo en http://localhost:5000")
    print("    Presiona Ctrl+C para detener.\n")

    open_browser_after(2)

    # Importamos Flask aquí porque ya estamos en el venv con todo instalado
    from app import app as flask_app
    flask_app.run(debug=False, port=5000, threaded=True)


if __name__ == "__main__":
    main()
