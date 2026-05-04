"""
ReadWorks Agent — Flask backend
================================
Automatiza la resolución de asignaciones de ReadWorks usando Playwright para
navegar el sitio y la API de Claude (Anthropic) para generar las respuestas.

Flujo principal:
  1. El usuario pega una URL de student-workspace en la UI.
  2. /solve lanza extract_and_solve() en un hilo aparte.
  3. El progreso se transmite al cliente via Server-Sent Events (SSE) en /progress/<id>.
  4. El agente navega a la pestaña de texto, extrae el pasaje, luego va a la pestaña
     de preguntas, extrae las opciones y textareas, llama a Claude, y rellena el formulario.
"""

import os
import json
import queue
import re
import threading
import traceback
import uuid

import anthropic
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request, stream_with_context

# ── Carga variables de entorno desde .env ──────────────────────────────────
load_dotenv()

app = Flask(__name__)

# ── Configuración global desde .env ────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
# HEADLESS=true oculta el navegador (útil en servidor); false lo muestra (recomendado en local).
HEADLESS = os.getenv("HEADLESS", "false").lower() == "true"
# AUTO_SUBMIT=true hace clic en "Submit" al terminar de rellenar las respuestas.
AUTO_SUBMIT = os.getenv("AUTO_SUBMIT", "false").lower() == "true"

# Diccionario en memoria que guarda las colas de progreso por task_id.
# Cada tarea crea su propia Queue; el hilo del agente produce, el SSE consume.
progress_queues: dict[str, queue.Queue] = {}


# ── Cliente de Anthropic ───────────────────────────────────────────────────

def get_client() -> anthropic.Anthropic:
    """Devuelve un cliente de Anthropic configurado con la API key del .env."""
    if not ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY no está configurada en el archivo .env")
    return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


# ── Prompt del sistema para respuestas humanizadas ────────────────────────
# Le indica a Claude que escriba como un estudiante real: variado, natural,
# con referencias al texto, sin sonar robótico ni repetitivo.
HUMANIZE_SYSTEM = """You are a student answering reading comprehension questions for a school assignment.
Your open-ended answers must:
- Sound like a real student wrote them — natural, genuine, not robotic
- Vary sentence structure and openings each time (never start them all the same way)
- Reference specific names, places, and events from the text
- Be 2–4 sentences per question — enough to show you understood, not a wall of text
- Use normal student vocabulary: clear but not overly academic
- Include natural transitions like "Also,", "For example,", "This shows that...", "Because of this..."
- Feel slightly different every single run — vary word choice, sentence order, angle
- Be in the same language as the questions (English if questions are in English)
Never sound like a summary. Sound like a student who actually read and thought about the story."""


def build_prompt(text_content: str, page_text: str, radio_data: dict, num_open: int) -> str:
    """
    Construye el prompt de usuario que se envía a Claude.

    Args:
        text_content: Texto completo del pasaje de lectura.
        page_text:    Texto de la página de preguntas (incluye enunciados).
        radio_data:   Dict {nombre_grupo: [{id, text}, ...]} con todas las opciones.
        num_open:     Cantidad de preguntas abiertas (textareas).

    Returns:
        Prompt listo para enviar a la API.
    """
    return f"""Read the passage and answer the comprehension questions below.

=== PASSAGE ===
{text_content[:5000]}

=== QUESTIONS (from page) ===
{page_text[:4000]}

=== MULTIPLE CHOICE OPTIONS (JSON) ===
{json.dumps(radio_data, ensure_ascii=False, indent=2)}

=== TASK ===
1. For each multiple-choice group pick the ID of the correct input.
2. For the {num_open} open-ended questions write natural student answers (see your persona instructions).

Reply ONLY with this JSON — no markdown, no extra text:
{{
  "radio_answers": {{ "group_name": "input_id" }},
  "text_answers": ["answer1", "answer2"]
}}"""


# ── Agente principal ───────────────────────────────────────────────────────

def extract_and_solve(url: str, task_id: str, auto_submit: bool = False) -> None:
    """
    Núcleo del agente. Corre en un hilo separado por cada solicitud.

    Pasos:
      1. Abre un contexto persistente de Chromium (guarda cookies entre sesiones).
      2. Navega a la pestaña de texto y extrae el pasaje.
      3. Navega a la pestaña de preguntas, mapea radio buttons y textareas.
      4. Llama a Claude para obtener las respuestas.
      5. Rellena el formulario con las respuestas generadas.
      6. Opcionalmente envía la asignación (auto_submit).

    Args:
        url:         URL completa del student-workspace de ReadWorks.
        task_id:     ID único de la tarea, usado para identificar su Queue.
        auto_submit: Si es True, hace clic en el botón Submit al terminar.
    """
    q = progress_queues[task_id]

    def send(msg: str, progress: int, **kwargs) -> None:
        """Encola un evento de progreso que el SSE transmitirá al cliente."""
        q.put({"message": msg, "progress": progress, **kwargs})

    try:
        # Playwright se importa aquí para no fallar al importar el módulo si no está instalado.
        from playwright.sync_api import sync_playwright

        send("Abriendo navegador...", 5)

        # browser_data/ guarda cookies y sesión entre ejecuciones.
        # Así el usuario solo necesita iniciar sesión en ReadWorks una vez.
        user_data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "browser_data")
        os.makedirs(user_data_dir, exist_ok=True)

        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir,
                headless=HEADLESS,
                viewport={"width": 1280, "height": 800},
                # --no-sandbox es necesario en algunos entornos Linux/Docker.
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            page = context.new_page()

            # ReadWorks usa hash-routing: #!wsTab:text/ y #!wsTab:questions/
            # son rutas del cliente, no del servidor.
            base_url = url.split("#")[0].rstrip("/")
            text_url      = base_url + "#!wsTab:text/"
            questions_url = base_url + "#!wsTab:questions/"

            send("Navegando a ReadWorks...", 12)
            page.goto(text_url, timeout=30000)
            page.wait_for_timeout(2500)

            # ── Detección de login ──
            # Si la página muestra "Log In" o "Sign In", el usuario no tiene sesión activa.
            body_text = page.inner_text("body")
            needs_login = "Log In" in body_text or "Sign In" in body_text

            if needs_login:
                # El agente espera hasta 3 minutos a que el usuario inicie sesión manualmente.
                send("🔐 Inicia sesión en la ventana del navegador y espera...", 10, needs_login=True)
                page.wait_for_function(
                    "() => !!document.querySelector('article, .article-body')",
                    timeout=180_000,
                )
                page.goto(text_url, timeout=30000)
                page.wait_for_timeout(2000)

            # ── Extracción del pasaje ──
            send("Leyendo el pasaje...", 28)
            try:
                page.wait_for_selector("article", timeout=10000)
            except Exception:
                pass  # Algunos pasajes no usan <article>; el fallback de JS lo maneja.

            # Prueba selectores en orden de preferencia; usa body como último recurso.
            text_content: str = page.evaluate("""() => {
                for (const sel of ['article', '.article-content', '[class*="passage"]', 'main']) {
                    const el = document.querySelector(sel);
                    if (el && el.innerText.trim().length > 200) return el.innerText.trim();
                }
                return document.body.innerText.substring(0, 8000);
            }""")

            # ── Navegación a preguntas ──
            send("Cargando preguntas...", 42)
            page.goto(questions_url, timeout=30000)
            page.wait_for_timeout(3000)

            # Texto plano de la página para que Claude lea los enunciados completos.
            page_text: str = page.evaluate(
                "() => (document.querySelector('main') || document.body).innerText"
            )

            # ── Mapeo de preguntas de opción múltiple ──
            send("Analizando estructura...", 54)

            # Agrupa cada radio button con su texto visible, organizado por name (= grupo de pregunta).
            radio_data: dict = page.evaluate("""() => {
                const radios = document.querySelectorAll('input[type="radio"]');
                const result = {};
                radios.forEach(r => {
                    const label = r.closest('label') || document.querySelector(`label[for="${r.id}"]`);
                    if (!result[r.name]) result[r.name] = [];
                    result[r.name].push({ id: r.id, text: label ? label.innerText.trim() : r.value });
                });
                return result;
            }""")

            # Cuenta las preguntas abiertas (textareas con clase .validated-input).
            num_open: int = page.evaluate(
                "() => document.querySelectorAll('textarea.validated-input').length"
            )
            num_mc = len(radio_data)
            send(f"Encontré {num_mc} de opción múltiple y {num_open} abiertas — generando respuestas...", 62)

            # ── Llamada a Claude ──
            # temperature=1 maximiza la variedad en las respuestas abiertas.
            client = get_client()
            message = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=4000,
                temperature=1,
                system=HUMANIZE_SYSTEM,
                messages=[{"role": "user", "content": build_prompt(text_content, page_text, radio_data, num_open)}],
            )

            # Limpia posibles bloques markdown que Claude incluya a veces.
            raw: str = message.content[0].text.strip()
            raw = re.sub(r"^```(?:json)?\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)
            answers: dict = json.loads(raw)

            # ── Relleno de opción múltiple ──
            send("Seleccionando respuestas...", 78)
            radio_results: list[str] = []

            for group, input_id in answers.get("radio_answers", {}).items():
                try:
                    el = page.query_selector(f"#{input_id}")
                    if el:
                        el.click()
                    # Guarda el texto de la opción elegida para mostrarlo en la UI.
                    options = radio_data.get(group, [])
                    chosen = next((o["text"] for o in options if o["id"] == input_id), input_id)
                    radio_results.append(chosen)
                except Exception as e:
                    radio_results.append(f"(error: {e})")

            # ── Relleno de preguntas abiertas ──
            send("Escribiendo respuestas abiertas...", 88)
            text_results: list[str] = answers.get("text_answers", [])
            textareas = page.query_selector_all("textarea.validated-input")

            for i, answer in enumerate(text_results):
                if i >= len(textareas):
                    break
                try:
                    # Se usa el setter nativo para activar el sistema reactivo de ReadWorks.
                    # Un simple textarea.value = "..." no dispara los eventos de React/Vue.
                    textareas[i].evaluate(
                        f"""(el) => {{
                            const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
                            setter.call(el, {json.dumps(answer)});
                            el.dispatchEvent(new Event('input',  {{ bubbles: true }}));
                            el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                        }}"""
                    )
                except Exception:
                    pass

            # ── Envío automático (opcional) ──
            if auto_submit or AUTO_SUBMIT:
                send("Enviando respuestas...", 95)
                try:
                    submit_btn = page.query_selector('button:has-text("Submit"), input[value="Submit"]')
                    if submit_btn:
                        submit_btn.click()
                        page.wait_for_timeout(2000)
                except Exception:
                    pass

            # Compila resultados en una lista unificada para la UI.
            all_results = (
                [{"type": "multiple", "answer": a} for a in radio_results]
                + [{"type": "open",     "answer": a} for a in text_results]
            )

            send(
                f"¡Todo listo! {len(radio_results)} de opción múltiple + {len(text_results)} abiertas.",
                100,
                done=True,
                results=all_results,
                submitted=auto_submit or AUTO_SUBMIT,
            )

            # Mantiene el navegador abierto 5 min para que el usuario revise y envíe.
            # En modo headless o auto-submit no es necesario esperar.
            if not (auto_submit or AUTO_SUBMIT) and not HEADLESS:
                threading.Event().wait(timeout=300)

            context.close()

    except Exception as e:
        send(f"Error: {str(e)}", -1, error=True, detail=traceback.format_exc())
    finally:
        # Limpia la queue de memoria una vez que la tarea termina.
        progress_queues.pop(task_id, None)


# ── Rutas Flask ────────────────────────────────────────────────────────────

@app.route("/")
def index():
    """Sirve la interfaz gráfica principal."""
    return render_template("index.html")


@app.route("/solve", methods=["POST"])
def solve():
    """
    Recibe la URL de ReadWorks y lanza el agente en un hilo daemon.

    Body JSON esperado:
        { "url": "https://...", "auto_submit": false }

    Returns:
        JSON con el task_id para que el cliente pueda suscribirse al SSE.
    """
    data = request.get_json(silent=True) or {}
    url = data.get("url", "").strip()
    auto_submit = bool(data.get("auto_submit", False))

    if not url or "readworks.org" not in url:
        return jsonify({"error": "Por favor ingresa una URL válida de ReadWorks."}), 400

    task_id = str(uuid.uuid4())
    progress_queues[task_id] = queue.Queue()

    thread = threading.Thread(
        target=extract_and_solve,
        args=(url, task_id, auto_submit),
        daemon=True,  # El hilo muere si el proceso principal termina.
    )
    thread.start()

    return jsonify({"task_id": task_id})


@app.route("/progress/<task_id>")
def progress_stream(task_id: str):
    """
    Stream de Server-Sent Events (SSE) para un task_id dado.
    El cliente se suscribe aquí y recibe actualizaciones en tiempo real
    mientras el agente trabaja en otro hilo.

    Emite eventos JSON con los campos: message, progress, done, error, results.
    Envía {"ping": true} cada 90 s para mantener viva la conexión.
    """
    def generate():
        q = progress_queues.get(task_id)
        if not q:
            yield f"data: {json.dumps({'error': 'Tarea no encontrada'})}\n\n"
            return

        while True:
            try:
                event = q.get(timeout=90)
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event.get("done") or event.get("error"):
                    break
            except queue.Empty:
                # Keepalive: evita que proxies o el navegador cierren la conexión.
                yield f"data: {json.dumps({'ping': True})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # Deshabilita buffering en Nginx.
            "Connection": "keep-alive",
        },
    )


# ── Entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n🚀 ReadWorks Agent listo")
    print("📌 Abre:  http://localhost:5000\n")
    # threaded=True permite manejar SSE y solicitudes normales en paralelo.
    app.run(debug=False, port=5000, threaded=True)
