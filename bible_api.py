# bible_api.py
# API Flask que extrae el texto de un pasaje bíblico (NWT) desde wol.jw.org,
# pensada para desplegarse gratis en PythonAnywhere y ser consumida desde
# tu app de notas (fetch por JavaScript).
#
# ---------------------------------------------------------------------------
# DESPLIEGUE EN PYTHONANYWHERE (plan gratuito):
#
# 1. Creá una cuenta en https://www.pythonanywhere.com
# 2. Consola Bash (menú "Consoles" -> "Bash") y corré:
#      pip install --user flask requests beautifulsoup4
# 3. Menú "Web" -> "Add a new web app" -> elegí "Flask" -> Python 3.10+
#    (esto te crea un proyecto con un archivo tipo /home/tuusuario/mysite/flask_app.py)
# 4. Reemplazá TODO el contenido de ese flask_app.py por el contenido de
#    este archivo (o subilo con este nombre y ajustá el WSGI, ver paso 5).
# 5. En la sección "Code" de la pestaña Web, fijate que el "WSGI configuration
#    file" importe la variable `app` de este módulo, algo como:
#      from flask_app import app as application
#    (si usaste el asistente de Flask de PythonAnywhere esto ya viene armado).
# 6. Botón verde "Reload" en la pestaña Web.
# 7. Probá en el navegador:
#    https://tuusuario.pythonanywhere.com/verse?lang=S&book=43&chapter=3&verse_start=16
#
# ---------------------------------------------------------------------------

from flask import Flask, request, jsonify
import requests
from bs4 import BeautifulSoup
import re
import time

app = Flask(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
}

# Prefijos de idioma/región/biblioteca que usa wol.jw.org en sus URLs.
# Agregá más si necesitás otros idiomas (podés sacarlos navegando wol.jw.org
# en ese idioma y mirando la URL de un capítulo).
LANG_CONFIG = {
    "S": {"path": "es", "lib": "r4", "prefix": "lp-s"},  # Español
    "E": {"path": "en", "lib": "r1", "prefix": "lp-e"},  # Inglés
}

# Cache muy simple en memoria: evita pedirle la misma página a wol.jw.org
# en cada tap del widget. Se pierde al reiniciar el proceso, lo cual es
# aceptable para este uso. TTL en segundos.
_CACHE = {}
_CACHE_TTL = 60 * 60 * 12  # 12 horas


def _cache_get(key):
    hit = _CACHE.get(key)
    if not hit:
        return None
    value, expires_at = hit
    if time.time() > expires_at:
        _CACHE.pop(key, None)
        return None
    return value


def _cache_set(key, value):
    _CACHE[key] = (value, time.time() + _CACHE_TTL)


def wol_chapter_url(lang, book, chapter, edition="nwtsty"):
    cfg = LANG_CONFIG.get(lang, LANG_CONFIG["S"])
    return f"https://wol.jw.org/{cfg['path']}/wol/b/{cfg['lib']}/{cfg['prefix']}/{edition}/{book}/{chapter}"


def clean_verse_text(text):
    # Quita marcas de referencias cruzadas ("+") y asteriscos de nota al pie
    # sueltos, que wol.jw.org intercala en el texto plano.
    text = re.sub(r"\s*\+", "", text)
    text = re.sub(r"\*(?=[\s.,;:!?”\"’)]|$)", "", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def extract_verses(html, book, chapter, verse_start, verse_end):
    soup = BeautifulSoup(html, "html.parser")
    container = soup.select_one("#bibleText") or soup

    results = []
    for v in range(verse_start, verse_end + 1):
        vid = f"v{int(book):02d}{int(chapter):03d}{v:03d}"
        el = container.find(id=vid)
        if not el:
            continue
        el = BeautifulSoup(str(el), "html.parser")  # copia para no mutar el árbol original
        # Quita el numerito de versículo/capítulo y notas/referencias que
        # vienen incrustadas como elementos aparte dentro del span del verso.
        for junk in el.select(".verseNum, .chapterNum, sup, .footnoteLink, .xrefLink, .fn, a.b"):
            junk.decompose()
        text = clean_verse_text(el.get_text(" "))
        if text:
            results.append({"verse": v, "text": text})
    return results


@app.route("/verse")
def verse():
    try:
        lang = request.args.get("lang", "S").upper()
        book = int(request.args.get("book"))
        chapter = int(request.args.get("chapter"))
        verse_start = int(request.args.get("verse_start", request.args.get("verse", 1)))
        verse_end = int(request.args.get("verse_end", verse_start))
        edition = request.args.get("edition", "nwtsty")
    except (TypeError, ValueError):
        return jsonify({
            "error": "Parámetros inválidos. Usa ?book=&chapter=&verse_start=&verse_end= (o &verse=)."
        }), 400

    if verse_end < verse_start or (verse_end - verse_start) > 60:
        return jsonify({"error": "Rango de versículos inválido o demasiado grande."}), 400

    cache_key = f"{lang}:{edition}:{book}:{chapter}:{verse_start}:{verse_end}"
    cached = _cache_get(cache_key)
    if cached:
        return jsonify(cached)

    url = wol_chapter_url(lang, book, chapter, edition)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        return jsonify({"error": f"No se pudo obtener la página de wol.jw.org: {e}", "source_url": url}), 502

    verses = extract_verses(resp.text, book, chapter, verse_start, verse_end)
    if not verses:
        return jsonify({
            "error": "No se encontraron versículos. Revisa book/chapter/verse, o wol.jw.org cambió su HTML "
                     "(ver sección de depuración en los comentarios del archivo).",
            "source_url": url,
        }), 404

    if len(verses) > 1:
        combined = " ".join(f"{v['verse']} {v['text']}" for v in verses)
    else:
        combined = verses[0]["text"]

    payload = {
        "book": book,
        "chapter": chapter,
        "verse_start": verse_start,
        "verse_end": verse_end,
        "verses": verses,
        "text": combined,
        "source_url": url,
    }
    _cache_set(cache_key, payload)
    return jsonify(payload)


@app.after_request
def add_cors_headers(resp):
    # Permite que tu app de notas (servida desde otro origen, o abierta
    # como archivo local) pueda hacer fetch() a esta API.
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


@app.route("/")
def index():
    return jsonify({
        "ok": True,
        "usage": "/verse?lang=S&book=43&chapter=3&verse_start=16&verse_end=16",
        "note": "book=número de libro 1-66 (Génesis=1 ... Apocalipsis=66), igual que en tu app de notas.",
    })


# ---------------------------------------------------------------------------
# DEPURACIÓN si algún día deja de encontrar versículos (cambió el HTML):
# entrá a este endpoint y mirá el HTML crudo que está llegando, buscá a mano
# el id real que usa wol.jw.org para un versículo (Ctrl+F por el número de
# versículo en el HTML) y ajustá el patrón de `vid` en extract_verses().
#
# @app.route("/debug-html")
# def debug_html():
#     book = int(request.args.get("book", 43))
#     chapter = int(request.args.get("chapter", 3))
#     lang = request.args.get("lang", "S").upper()
#     url = wol_chapter_url(lang, book, chapter)
#     resp = requests.get(url, headers=HEADERS, timeout=10)
#     return resp.text, 200, {"Content-Type": "text/plain; charset=utf-8"}
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(debug=True)
