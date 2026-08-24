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
    # wol.jw.org está detrás de Akamai (protección anti-bot). Sin el header
    # "Accept" completo, Akamai puede detectar el request como bot y servir
    # una versión reducida de la página (sin el contenido bíblico real).
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
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
    # wol.jw.org ya no usa #bibleText como contenedor ni ids del tipo
    # "v43003016" (dígitos pegados). Ahora el marcado es
    # <span class="v" id="v43-3-16">...</span> dentro de #article, con
    # book-chapter-verse separados por guiones y SIN ceros a la izquierda.
    container = soup.select_one("#article") or soup

    results = []
    for v in range(verse_start, verse_end + 1):
        vid = f"v{int(book)}-{int(chapter)}-{v}"
        el = container.find("span", class_="v", id=vid)
        if not el:
            # fallback por si algún día vuelven al formato viejo o cambian de nuevo
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
# DEPURACIÓN: en vez de devolver el HTML completo (que es larguísimo y se
# trunca al inspeccionarlo), busca en el servidor los ids con pinta de
# versículo (v + dígitos) y algunos contenedores típicos, y devuelve un
# resumen chiquito en JSON. Es de solo lectura. Podés comentarla de nuevo
# una vez que ya no la necesites.
@app.route("/debug-verse")
def debug_verse():
    book = int(request.args.get("book", 43))
    chapter = int(request.args.get("chapter", 3))
    lang = request.args.get("lang", "S").upper()
    url = wol_chapter_url(lang, book, chapter)
    resp = requests.get(url, headers=HEADERS, timeout=10)
    soup = BeautifulSoup(resp.text, "html.parser")

    # Diagnóstico ampliado: en vez de adivinar el patrón de antemano,
    # devolvemos TODOS los ids que contengan dígitos (para ver el formato
    # real tal cual está hoy en la página) y buscamos manualmente "43" y "3"
    # (book=43, chapter=3) en cualquier posición dentro del id.
    all_ids_with_digits = [
        el.get("id") for el in soup.find_all(id=True)
        if any(ch.isdigit() for ch in el.get("id"))
    ][:60]

    ids_mentioning_book_chapter = [
        i for i in all_ids_with_digits if "43" in i and "3" in i
    ][:30]

    verse_like = soup.find_all("span", class_="v", id=re.compile(r"^v\d+-\d+-\d+$"))
    if not verse_like:
        verse_like = soup.find_all(id=re.compile(r"^v\d{8,9}$"))
    verse_ids = [el.get("id") for el in verse_like][:20]

    # Contenedores típicos de texto bíblico en distintas versiones del sitio.
    candidate_selectors = ["#bibleText", ".bibleText", "#article", ".docSubContent", ".contentBox", "article"]
    containers_found = {sel: bool(soup.select_one(sel)) for sel in candidate_selectors}

    # ¿Existe algún elemento con class="v" en cualquier lado, sin importar el id?
    class_v_elements = soup.find_all(class_="v")
    class_v_sample = [str(el)[:200] for el in class_v_elements[:5]]

    sample = None
    if verse_like:
        # Devolvemos el HTML del primer verso encontrado, para ver su estructura interna.
        sample = str(verse_like[0])[:1500]

    return jsonify({
        "source_url": url,
        "http_status": resp.status_code,
        "page_title": soup.title.string if soup.title else None,
        "total_elements_with_id": len(soup.find_all(id=True)),
        "verse_like_ids_found": verse_ids,
        "all_ids_with_digits_sample": all_ids_with_digits,
        "ids_mentioning_book_and_chapter": ids_mentioning_book_chapter,
        "containers_found": containers_found,
        "class_v_elements_found": len(class_v_elements),
        "class_v_sample": class_v_sample,
        "first_verse_like_html_sample": sample,
        # Diagnóstico extra: tamaño real del HTML recibido y una muestra cruda.
        # Si html_length es chico (unos pocos KB) y raw_html_sample muestra un
        # challenge/captcha de Akamai en vez de contenido de la Biblia, confirma
        # que el bloqueo es anti-bot y no un problema de selectores.
        "html_length": len(resp.text),
        "raw_html_sample": resp.text[:1200],
    })
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# DEPURACIÓN 2: prueba la API oficial de descargas de publicaciones de JW.org
# (la misma que usa la app JW Library), a ver si nos da un link de descarga
# usable para el libro completo en vez de tener que scrapear wol.jw.org.
# Es de solo lectura. Podés comentarla una vez que ya no la necesites.
@app.route("/debug-pubapi")
def debug_pubapi():
    book = request.args.get("book", "43")
    lang = request.args.get("lang", "S").upper()
    pub = request.args.get("pub", "nwt")
    fileformat = request.args.get("fileformat", "RTF")

    url = "https://b.jw-cdn.org/apis/pub-media/GETPUBMEDIALINKS"
    params = {
        "output": "json",
        "pub": pub,
        "booknum": book,
        "langwritten": lang,
        "fileformat": fileformat,
    }
    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=10)
    except requests.RequestException as e:
        return jsonify({"error": f"No se pudo contactar la API: {e}", "url": url}), 502

    try:
        data = resp.json()
    except ValueError:
        data = None

    return jsonify({
        "requested_url": resp.url,
        "http_status": resp.status_code,
        "json_response": data,
        "raw_text_sample": None if data is not None else resp.text[:1000],
    })
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(debug=True)
