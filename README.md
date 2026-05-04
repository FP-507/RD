# ReadWorks Agent 📚

Agente que resuelve asignaciones de ReadWorks automáticamente. Pega el link, haz clic en **Resolver** y el agente lee el pasaje, genera las respuestas con IA y las rellena en el formulario.

---

## Demo rápido

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

## Despliegue en la nube (Render + GitHub)

> Esto te da una URL permanente accesible desde cualquier navegador.

### 1 · Sube el proyecto a GitHub

```bash
git init
git add .
git commit -m "first commit"
git remote add origin https://github.com/TU-USUARIO/RD.git
git push -u origin main
```

### 2 · Crea el servicio en Render

1. Ve a [render.com](https://render.com) e inicia sesión con tu cuenta de GitHub
2. Haz clic en **New → Web Service**
3. Selecciona el repo `RD`
4. Render detecta el `render.yaml` automáticamente — no necesitas configurar nada más
5. En la sección **Environment Variables** agrega:
   ```
   ANTHROPIC_API_KEY = sk-ant-...
   ```
6. Haz clic en **Deploy**

### 3 · Accede a tu app

Render te da una URL del tipo:
```
https://readworks-agent.onrender.com
```

Esa URL funciona desde cualquier dispositivo y se actualiza automáticamente cada vez que haces `git push`.

> **Nota sobre el login:** La sesión del navegador se guarda en `browser_data/`. En Render free tier el disco es efímero, así que si el servidor se reinicia tendrás que iniciar sesión en ReadWorks de nuevo la primera vez que uses la app.

---

## Uso local (Windows)

```bash
# Doble clic en start.bat
# O desde la terminal:
python app.py
```

Abre [http://localhost:5000](http://localhost:5000) en tu navegador.

---

## Configuración (`.env`)

Copia `.env.example` como `.env` y rellena tus datos:

| Variable           | Descripción                                            | Default  |
|--------------------|--------------------------------------------------------|----------|
| `ANTHROPIC_API_KEY`| API key de Anthropic — **obligatoria**                 | —        |
| `HEADLESS`         | `true` oculta el navegador (usar en servidor)          | `false`  |
| `AUTO_SUBMIT`      | `true` envía la asignación automáticamente al terminar | `false`  |

---

## Estructura del proyecto

```
RD/
├── app.py              # Backend Flask + lógica del agente
├── templates/
│   └── index.html      # Interfaz gráfica
├── build.sh            # Script de build para Render
├── render.yaml         # Configuración de despliegue en Render
├── Procfile            # Comando de inicio (Railway / Heroku)
├── requirements.txt    # Dependencias de Python
├── start.bat           # Lanzador local para Windows
├── .env                # Variables privadas (no subir — en .gitignore)
├── .env.example        # Plantilla de configuración
└── .gitignore
```

---

## Requisitos

- Python 3.10+
- API key de [Anthropic](https://console.anthropic.com)
