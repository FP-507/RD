"""
ReadWorks Agent — Flask backend
================================
Automatiza la resolución de asignaciones de ReadWorks usando Selenium para
navegar el sitio y la API de Claude (Anthropic) para generar las respuestas.

Se usa Selenium en lugar de Playwright para evitar la dependencia de greenlet
que causa problemas de DLL en Windows con Python 3.13.
"""

import json
import os
import queue
import re
import threading
import time
import traceback
import uuid

import anthropic
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request, stream_with_context

load_dotenv()

app = Flask(__name__)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
HEADLESS          = os.getenv("HEADLESS", "false").lower() == "true"
AUTO_SUBMIT       = os.getenv("AUTO_SUBMIT", "false").lower() == "true"

progress_queues: dict[str, queue.Queue] = {}


# ── Cliente de Anthropic ───────────────────────────────────────────────────

def get_client() -> anthropic.Anthropic:
    if not ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY no está configurada en el archivo .env")
    return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


# ── Prompt del sistema ─────────────────────────────────────────────────────

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


# ── Detección y apertura de navegador ─────────────────────────────────────

def _open_driver(user_data_dir: str):
    """
    Intenta abrir Chrome primero; si no está instalado, usa Edge.
    Edge viene preinstalado en Windows 10/11, así que siempre hay un fallback.
    Devuelve (driver, nombre_navegador).
    """
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    from selenium.webdriver.chrome.service import Service as ChromeService
    from selenium.webdriver.edge.options import Options as EdgeOptions
    from selenium.webdriver.edge.service import Service as EdgeService
    from webdriver_manager.chrome import ChromeDriverManager
    from webdriver_manager.microsoft import EdgeChromiumDriverManager

    common_args = [
        f"--user-data-dir={user_data_dir}",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--window-size=1280,800",
    ]
    if HEADLESS:
        common_args.append("--headless=new")

    # ── Intento 1: Chrome ──
    try:
        opts = ChromeOptions()
        for a in common_args:
            opts.add_argument(a)
        driver = webdriver.Chrome(
            service=ChromeService(ChromeDriverManager().install()),
            options=opts,
        )
        return driver, "Chrome"
    except Exception:
        pass

    # ── Intento 2: Edge (preinstalado en Windows 10/11) ──
    opts = EdgeOptions()
    for a in common_args:
        opts.add_argument(a)
    driver = webdriver.Edge(
        service=EdgeService(EdgeChromiumDriverManager().install()),
        options=opts,
    )
    return driver, "Edge"


# ── Agente principal (Selenium) ────────────────────────────────────────────

def extract_and_solve(url: str, task_id: str, auto_submit: bool = False) -> None:
    """
    Abre Chrome o Edge automáticamente, navega ReadWorks, extrae las preguntas,
    consulta a Claude y rellena el formulario.
    webdriver-manager descarga el driver compatible — no requiere instalación manual.
    """
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    q = progress_queues[task_id]

    def send(msg: str, progress: int, **kwargs) -> None:
        q.put({"message": msg, "progress": progress, **kwargs})

    driver = None
    try:
        send("Abriendo navegador...", 5)

        # Carpeta de perfil para mantener la sesión de ReadWorks entre ejecuciones
        user_data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "browser_data")
        os.makedirs(user_data_dir, exist_ok=True)

        driver, browser_name = _open_driver(user_data_dir)
        send(f"Navegador detectado: {browser_name}", 8)

        base_url      = url.split("#")[0].rstrip("/")
        text_url      = base_url + "#!wsTab:text/"
        questions_url = base_url + "#!wsTab:questions/"

        send("Navegando a ReadWorks...", 12)
        driver.get(text_url)
        time.sleep(3)

        # ── Detección de login ──
        body_text   = driver.find_element(By.TAG_NAME, "body").text
        needs_login = "Log In" in body_text or "Sign In" in body_text

        if needs_login:
            send("🔐 Inicia sesión en la ventana del navegador y espera...", 10, needs_login=True)
            WebDriverWait(driver, 180).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "article, .article-body"))
            )
            driver.get(text_url)
            time.sleep(2)

        # ── Extracción del pasaje ──
        send("Leyendo el pasaje...", 28)
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.TAG_NAME, "article"))
            )
        except Exception:
            pass

        text_content: str = driver.execute_script("""
            for (const sel of ['article', '.article-content', '[class*="passage"]', 'main']) {
                const el = document.querySelector(sel);
                if (el && el.innerText.trim().length > 200) return el.innerText.trim();
            }
            return document.body.innerText.substring(0, 8000);
        """)

        # ── Navegación a preguntas ──
        send("Cargando preguntas...", 42)
        driver.get(questions_url)
        time.sleep(3)

        page_text: str = driver.execute_script(
            "return (document.querySelector('main') || document.body).innerText"
        )

        # ── Mapeo de preguntas ──
        send("Analizando estructura...", 54)

        radio_data: dict = driver.execute_script("""
            const radios = document.querySelectorAll('input[type="radio"]');
            const result = {};
            radios.forEach(r => {
                const label = r.closest('label') || document.querySelector(`label[for="${r.id}"]`);
                if (!result[r.name]) result[r.name] = [];
                result[r.name].push({ id: r.id, text: label ? label.innerText.trim() : r.value });
            });
            return result;
        """)

        num_open: int = driver.execute_script(
            "return document.querySelectorAll('textarea.validated-input').length"
        )
        num_mc = len(radio_data)
        send(f"Encontré {num_mc} de opción múltiple y {num_open} abiertas — generando respuestas...", 62)

        # ── Llamada a Claude ──
        client = get_client()
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4000,
            temperature=1,
            system=HUMANIZE_SYSTEM,
            messages=[{"role": "user", "content": build_prompt(text_content, page_text, radio_data, num_open)}],
        )

        raw: str = message.content[0].text.strip()
        raw = re.sub(r"^```(?:json)?\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
        answers: dict = json.loads(raw)

        # ── Relleno de opción múltiple ──
        send("Seleccionando respuestas...", 78)
        radio_results: list[str] = []

        for group, input_id in answers.get("radio_answers", {}).items():
            try:
                el = driver.find_element(By.ID, input_id)
                # Usar JS click para evitar problemas con elementos fuera del viewport
                driver.execute_script("arguments[0].click();", el)
                opts   = radio_data.get(group, [])
                chosen = next((o["text"] for o in opts if o["id"] == input_id), input_id)
                radio_results.append(chosen)
            except Exception as e:
                radio_results.append(f"(error: {e})")

        # ── Relleno de preguntas abiertas ──
        send("Escribiendo respuestas abiertas...", 88)
        text_results: list[str] = answers.get("text_answers", [])
        textareas = driver.find_elements(By.CSS_SELECTOR, "textarea.validated-input")

        for i, answer in enumerate(text_results):
            if i >= len(textareas):
                break
            try:
                # Setter nativo para activar el sistema reactivo de ReadWorks
                driver.execute_script(
                    f"""
                    const el = arguments[0];
                    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
                    setter.call(el, {json.dumps(answer)});
                    el.dispatchEvent(new Event('input',  {{ bubbles: true }}));
                    el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    """,
                    textareas[i],
                )
            except Exception:
                pass

        # ── Envío automático ──
        if auto_submit or AUTO_SUBMIT:
            send("Enviando respuestas...", 95)
            try:
                btns = driver.find_elements(By.XPATH, "//button[contains(text(),'Submit')]")
                if btns:
                    btns[0].click()
                    time.sleep(2)
            except Exception:
                pass

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

        # Mantiene el navegador abierto 5 min para que el usuario revise y envíe
        if not (auto_submit or AUTO_SUBMIT) and not HEADLESS:
            time.sleep(300)

    except Exception as e:
        send(f"Error: {str(e)}", -1, error=True, detail=traceback.format_exc())
    finally:
        progress_queues.pop(task_id, None)
        if driver and (HEADLESS or auto_submit or AUTO_SUBMIT):
            try:
                driver.quit()
            except Exception:
                pass


# ── Rutas Flask ────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/solve", methods=["POST"])
def solve():
    data        = request.get_json(silent=True) or {}
    url         = data.get("url", "").strip()
    auto_submit = bool(data.get("auto_submit", False))

    if not url or "readworks.org" not in url:
        return jsonify({"error": "Por favor ingresa una URL válida de ReadWorks."}), 400

    task_id = str(uuid.uuid4())
    progress_queues[task_id] = queue.Queue()

    threading.Thread(
        target=extract_and_solve,
        args=(url, task_id, auto_submit),
        daemon=True,
    ).start()

    return jsonify({"task_id": task_id})


@app.route("/progress/<task_id>")
def progress_stream(task_id: str):
    def generate():
        q = progress_queues.get(task_id)
        if not q:
            yield f"data: {json.dumps({'error': True, 'message': 'Tarea no encontrada'})}\n\n"
            return
        while True:
            try:
                event = q.get(timeout=90)
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event.get("done") or event.get("error"):
                    break
            except queue.Empty:
                yield f"data: {json.dumps({'ping': True})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


if __name__ == "__main__":
    print("\n🚀 ReadWorks Agent listo")
    print("📌 Abre:  http://localhost:5000\n")
    app.run(debug=False, port=5000, threaded=True)
