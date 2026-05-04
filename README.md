# ReadWorks Agent 📚

Agente que resuelve asignaciones de ReadWorks automáticamente. Pega el link, haz clic en **Resolver** y el agente lee el pasaje, genera las respuestas con IA y las rellena en el formulario.

---

## Cómo funciona

```
URL de ReadWorks
      ↓
Playwright abre el navegador
      ↓
Extrae el pasaje de texto
      ↓
Mapea preguntas (opción múltiple + abiertas)
      ↓
Claude genera las respuestas
      ↓
El agente rellena el formulario
```

---

## Requisitos

- Python 3.10 o superior
- Cuenta en [Anthropic Console](https://console.anthropic.com) para obtener la API key

---

## Instalación

```bash
# 1. Clonar el repositorio
git clone https://github.com/tu-usuario/RD.git
cd RD

# 2. Instalar dependencias
pip install -r requirements.txt

# 3. Instalar el navegador de Playwright
playwright install chromium

# 4. Configurar variables de entorno
cp .env.example .env
# Edita .env y agrega tu ANTHROPIC_API_KEY
```

---

## Configuración (`.env`)

| Variable           | Descripción                                               | Default  |
|--------------------|-----------------------------------------------------------|----------|
| `ANTHROPIC_API_KEY`| API key de Anthropic — **obligatoria**                    | —        |
| `HEADLESS`         | `true` oculta el navegador, `false` lo muestra            | `false`  |
| `AUTO_SUBMIT`      | `true` envía la asignación automáticamente al terminar    | `false`  |

---

## Uso

### Windows
Doble clic en `start.bat` — instala todo y abre el servidor.

### Manual
```bash
python app.py
```

Luego abre [http://localhost:5000](http://localhost:5000) en tu navegador.

### Pasos en la UI
1. Abre tu asignación en ReadWorks y copia la URL
2. Pégala en el campo y haz clic en **Resolver ✦**
3. Si es la primera vez, inicia sesión en la ventana del navegador que se abre
4. Espera a que el agente termine — el progreso se muestra en tiempo real
5. Revisa las respuestas y haz clic en **Submit** en ReadWorks

> **Primera sesión:** el agente guarda las cookies en `browser_data/` — las siguientes veces no pide login.

---

## Estructura del proyecto

```
RD/
├── app.py                 # Backend Flask + lógica del agente
├── templates/
│   └── index.html         # Interfaz gráfica (UI)
├── browser_data/          # Sesión del navegador (generada automáticamente, no subir)
├── .env                   # Variables de entorno privadas (no subir)
├── .env.example           # Plantilla de configuración
├── .gitignore
├── requirements.txt
└── start.bat              # Lanzador para Windows
```

---

## Despliegue en servidor (Render / Railway)

1. Sube el proyecto a GitHub (`.env` y `browser_data/` ya están en `.gitignore`)
2. Crea un nuevo servicio Web en [Render](https://render.com) o [Railway](https://railway.app)
3. Agrega las variables de entorno desde el dashboard:
   - `ANTHROPIC_API_KEY` = tu key
   - `HEADLESS` = `true`
4. Usa como comando de inicio: `python app.py`

> En servidor siempre usa `HEADLESS=true` — no hay pantalla disponible.

---

## Dependencias

| Paquete        | Uso                                      |
|----------------|------------------------------------------|
| `flask`        | Servidor web y rutas HTTP                |
| `anthropic`    | Cliente oficial de la API de Claude      |
| `playwright`   | Automatización del navegador Chromium    |
| `python-dotenv`| Carga de variables desde `.env`          |
