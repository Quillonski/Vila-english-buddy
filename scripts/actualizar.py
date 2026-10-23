"""Descarga frases con audio (Tatoeba), palabras por tema (Datamuse), traducciones
(Wiktionary) y pronunciaciones grabadas por personas (Wikimedia Commons), y las
guarda en data/. La app las lee desde el mismo sitio, así el navegador no bloquea nada.
Lo ejecuta GitHub Actions; no hace falta correrlo a mano.

Versión optimizada:
  1. Traducciones en lote (hasta 50 palabras por consulta a Wiktionary).
  2. Consultas en paralelo, con un límite por servicio para no saturarlos.
  3. Ejemplos buscados solo entre las frases ya descargadas (sin consultas extra).
  4. Memoria (data/cache.json): traducciones y voces de semanas anteriores.
  5. Presupuesto de tiempo: si se acerca el límite, guarda lo que lleva y termina bien.
"""
import json, random, re, threading, time, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
UA = {"User-Agent": "ingles-para-vila/1.0 (proyecto personal de práctica de inglés)"}
INICIO = time.monotonic()
PRESUPUESTO = 40 * 60          # segundos; el workflow corta a los 60 min
REVISAR_NEGATIVOS = 30         # días antes de volver a buscar algo que no se encontró

TOPIC_SEEDS = {
    "Crimen y juicios": ["crime", "court", "police", "prison", "trial", "jury", "evidence", "theft"],
    "Abogados": ["lawyer", "law", "judge", "contract", "court", "lawsuit", "client", "verdict"],
    "Laboratorio y química": ["chemistry", "laboratory", "chemical", "science", "experiment", "acid", "molecule", "formula"],
    "Flores y jardín": ["flower", "garden", "plant", "bloom", "seed", "soil", "leaf", "petal"],
    "Libros de suspenso": ["book", "mystery", "secret", "story", "novel", "plot", "suspense", "author"],
    "Terror": ["ghost", "fear", "dark", "scary", "nightmare", "monster", "blood", "haunted"],
    "Detectives": ["detective", "clue", "murder", "investigation", "suspect", "witness", "alibi", "spy"],
    "Viajes": ["travel", "airport", "hotel", "trip", "ticket", "luggage", "passport", "beach"],
    "Emociones": ["emotion", "feeling", "happy", "sad", "angry", "afraid", "love", "proud"],
    "Día a día": ["home", "kitchen", "shopping", "work", "family", "morning", "clothes", "money"],
}

# cuántas consultas a la vez se permiten por servicio (para ser respetuosos)
LIMITES = {"tatoeba": 4, "datamuse": 6, "wiktionary": 3, "wikimedia": 3, "mymemory": 1}
_sem = {k: threading.Semaphore(v) for k, v in LIMITES.items()}
_print = threading.Lock()


def log(*a):
    with _print:
        print(*a, flush=True)


def queda_tiempo(margen=0):
    return time.monotonic() - INICIO < PRESUPUESTO - margen


def servicio(url):
    for k in LIMITES:
        if k in url:
            return _sem[k]
    return threading.Semaphore(4)


def get_json(url, tries=3, timeout=30):
    sem = servicio(url)
    for i in range(tries):
        try:
            with sem:
                with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
                    return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code == 429:
                time.sleep(5 * (i + 1))
                continue
            log("  HTTP", e.code, url[:120])
            if e.code >= 500 and i >= 1:
                return None
        except Exception as e:  # red inestable: reintentar
            log("  error", e, url[:120])
        time.sleep(2 * (i + 1))
    return None


def en_paralelo(fn, items, hilos=8):
    with ThreadPoolExecutor(max_workers=hilos) as ex:
        return list(ex.map(fn, items))


def lotes_con_reintento(fn, items, tam, hilos=3):
    """Corre fn sobre lotes de `items`. Si un lote falla (None), espera y lo reintenta en mitades
    más pequeñas, hasta lotes de 3. Devuelve la lista de resultados que sí funcionaron."""
    pendientes = [items[i:i + tam] for i in range(0, len(items), tam)]
    buenos = []
    for ronda in range(4):
        if not pendientes:
            break
        if ronda:
            log(f"  reintentando {len(pendientes)} lote(s) que fallaron")
            time.sleep(5 * ronda)
        fallidos = []
        for lote, r in zip(pendientes, en_paralelo(fn, pendientes, hilos=hilos)):
            if r is None:
                fallidos.append(lote)
            else:
                buenos.append(r)
        pendientes = []
        for lote in fallidos:
            if len(lote) > 3:
                mitad = len(lote) // 2
                pendientes += [lote[:mitad], lote[mitad:]]
            else:
                pendientes.append(lote)
    return buenos


def flat(x):
    if isinstance(x, list):
        for y in x:
            yield from flat(y)
    elif isinstance(x, dict):
        yield x


# ---------- memoria entre semanas ----------
HOY = date.today().isoformat()
VERSION_TRAD = 2  # súbelo si cambia la forma de elegir traducciones, para buscarlas de nuevo
cache = {"v": VERSION_TRAD, "es": {}, "audio": {}}


def cargar_cache():
    misma_version = False
    try:
        c = json.loads((DATA / "cache.json").read_text(encoding="utf-8"))
        misma_version = c.get("v") == VERSION_TRAD
        if misma_version:
            cache["es"].update(c.get("es", {}))
        cache["audio"].update(c.get("audio", {}))
    except Exception:
        pass
    # voces (y, si el método no cambió, traducciones) de la versión anterior de palabras.json
    try:
        for lst in json.loads((DATA / "palabras.json").read_text(encoding="utf-8")).values():
            for c in lst:
                if misma_version and c.get("es") and c["word"] not in cache["es"]:
                    cache["es"][c["word"]] = [c["es"], HOY]
                if c.get("audio") and c["word"] not in cache["audio"]:
                    cache["audio"][c["word"]] = [c["audio"], HOY]
    except Exception:
        pass


def en_cache(tipo, word):
    """Devuelve el valor guardado, o None si hay que buscarlo (o volver a buscarlo)."""
    v = cache[tipo].get(word)
    if not v:
        return None
    valor, fecha = v
    if valor:
        return valor
    try:  # un "no encontrado" se vuelve a revisar cada cierto tiempo
        if date.fromisoformat(fecha) > date.today() - timedelta(days=REVISAR_NEGATIVOS):
            return ""
    except ValueError:
        pass
    return None


def guardar_cache():
    cache["v"] = VERSION_TRAD
    (DATA / "cache.json").write_text(json.dumps(cache, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


# ---------- frases con audio ----------
def a_frase(s):
    if not s.get("audios"):
        return None
    a = s["audios"][0]
    es = next((t.get("text") for t in flat(s.get("translations", [])) if t.get("lang") == "spa"), "")
    if not es:
        return None
    return {
        "id": s["id"], "text": s.get("text", ""), "es": es,
        "audio": a.get("download_url") or f"https://audio.tatoeba.org/sentences/eng/{s['id']}.mp3",
        "author": a.get("author") or "", "license": a.get("license") or a.get("licence") or "",
        "attr": a.get("attribution_url") or "",
    }


def consulta_tatoeba(params):
    q = urllib.parse.urlencode({"lang": "eng", "has_audio": "yes", "trans:lang": "spa", "include": "audios",
                                "sort": "random", **params})
    d = get_json("https://api.tatoeba.org/v1/sentences?" + q, timeout=45) or {}
    return [f for f in (a_frase(s) for s in d.get("data", [])) if f]


def sin_repetir(listas):
    out, seen = [], set()
    for l in listas:
        for f in l:
            if f["id"] not in seen:
                seen.add(f["id"])
                out.append(f)
    return out


def banco_general(rondas=40):
    """Frases variadas con audio y traducción, para buscar temas que Vila escriba a mano."""
    return sin_repetir(en_paralelo(lambda _: consulta_tatoeba({"word_count": "4-12", "limit": "100"}), range(rondas)))


def frases_de(seeds):
    out = sin_repetir(en_paralelo(lambda s: consulta_tatoeba({"q": s, "word_count": "4-13", "limit": "40"}), seeds))
    random.shuffle(out)
    return out[:120]


# ---------- palabras: datos, voz real, traducción y ejemplos ----------
POS = {"n": "noun", "v": "verb", "adj": "adjective", "adv": "adverb"}


def info_de_datamuse(r):
    tags = r.get("tags", [])
    ipa = next((t[9:] for t in tags if t.startswith("ipa_pron:")), "")
    defs = r.get("defs") or []
    pos, definition = "", ""
    if defs:
        parts = defs[0].split("\t", 1)
        if len(parts) == 2:
            pos, definition = POS.get(parts[0], parts[0]), parts[1]
        else:
            definition = parts[0]
    if not pos:
        pos = next((POS[t] for t in tags if t in POS), "")
    return {"word": r.get("word", ""), "ipa": "/" + ipa + "/" if ipa else "", "def": definition.strip(), "pos": pos,
            "example": "", "es_example": "", "audio": ""}


def voces_reales(words):
    """Grabaciones de Wikimedia Commons (archivos En-us-<palabra>.ogg), en mp3 cuando existe. En lote y con memoria."""
    out = {}
    falta = []
    for w in dict.fromkeys(w for w in words if re.fullmatch(r"[a-z]+", w)):
        v = en_cache("audio", w)
        if v is None:
            falta.append(w)
        elif v:
            out[w] = v

    def lote(ws):
        titles = "|".join("File:En-us-" + w + ".ogg" for w in ws)
        q = urllib.parse.urlencode({"action": "query", "titles": titles, "prop": "videoinfo",
                                    "viprop": "url|derivatives", "format": "json", "formatversion": "2"})
        d = get_json("https://commons.wikimedia.org/w/api.php?" + q)
        if d is None:
            return None  # error de red: no anotar como "no existe"
        found = {}
        for page in d.get("query", {}).get("pages", []):
            m = re.match(r"File:En-us-(.+)\.ogg$", page.get("title", ""), re.I)
            if not m or page.get("missing") or not page.get("videoinfo"):
                continue
            vi = page["videoinfo"][0]
            mp3 = next((x.get("src") for x in vi.get("derivatives", [])
                        if "mpeg" in (x.get("type") or "") or x.get("transcodekey") == "mp3"), "")
            url = mp3 or vi.get("url", "")
            if url.startswith("//"):
                url = "https:" + url
            if url:
                found[m.group(1).lower()] = url
        return ws, found

    for ws, found in lotes_con_reintento(lote, falta, 40, hilos=3):
        for w in ws:
            cache["audio"][w] = [found.get(w, ""), HOY]
            if found.get(w):
                out[w] = found[w]
    return out


POS_TITULO = {"noun": "noun", "verb": "verb", "adjective": "adjective", "adverb": "adverb"}


def _primera_es(texto):
    m = re.search(r"\{\{tt?\+?\|es\|([^|}]+)", texto)
    return m.group(1).strip() if m else ""


def es_de_wikitext(text, pos=""):
    """Primera traducción al español del sentido que corresponde al tipo de palabra.
    Devuelve (traducción, hay_que_ver_subpágina)."""
    m = re.search(r"==English==(.*?)(\n==[^=]|\Z)", text, re.S)
    eng = m.group(1) if m else text
    sub = "translation subpage" in eng
    heads = [(h.start(), len(h.group(1)), h.group(2).strip().lower())
             for h in re.finditer(r"^(={3,6})\s*([^=\n]+?)\s*\1\s*$", eng, re.M)]
    objetivo = POS_TITULO.get(pos)
    if objetivo:
        for i, (ini, nivel, nombre) in enumerate(heads):
            if nombre != objetivo:
                continue
            fin = next((s for s, n, _ in heads[i + 1:] if n <= nivel), len(eng))
            seccion = eng[ini:fin]
            t = _primera_es(seccion)
            if t:
                return t, False
            if "translation subpage" in seccion:
                return "", True
    return _primera_es(eng), sub


def wikitextos(titulos):
    """Contenido de hasta 50 páginas de Wiktionary por consulta. Devuelve {título pedido: wikitext} o None si falló."""
    q = urllib.parse.urlencode({"action": "query", "prop": "revisions", "rvprop": "content", "rvslots": "main",
                                "titles": "|".join(titulos), "redirects": "1", "format": "json", "formatversion": "2"})
    d = get_json("https://en.wiktionary.org/w/api.php?" + q)
    if d is None:
        return None
    qd = d.get("query", {})
    alias = {t: t for t in titulos}
    for k in ("normalized", "redirects"):
        for x in qd.get(k, []):
            for orig, actual in list(alias.items()):
                if actual == x.get("from"):
                    alias[orig] = x.get("to")
    por_titulo = {}
    for p in qd.get("pages", []):
        revs = p.get("revisions") or []
        if revs:
            por_titulo[p.get("title")] = ((revs[0].get("slots") or {}).get("main") or {}).get("content", "")
    return {orig: por_titulo.get(actual, "") for orig, actual in alias.items()}


mymemory_chars = [0]


def traducir_todas(cands):
    """Traduce al español en lote: Wiktionary (20 por consulta, con reintentos), subpáginas, y MyMemory de respaldo.
    `cands` son los dicts de palabras (se usa su tipo para elegir el sentido correcto)."""
    pos_de = {c["word"]: c.get("pos", "") for c in cands}
    res, falta = {}, []
    for w in pos_de:
        v = en_cache("es", w)
        if v is None:
            falta.append(w)
        elif v:
            res[w] = v
    if not falta:
        return res
    textos = {}
    for r in lotes_con_reintento(wikitextos, falta, 20, hilos=3):
        textos.update(r)
    sub = []
    for w in falta:
        if w not in textos:
            continue  # error de red: se reintenta la próxima vez
        es, ver_sub = es_de_wikitext(textos[w], pos_de[w])
        if es:
            res[w] = es
        elif ver_sub:
            sub.append(w)
    if sub:
        for r in lotes_con_reintento(wikitextos, [w + "/translations" for w in sub], 20, hilos=3):
            for t, txt in r.items():
                w = t.split("/")[0]
                es, _ = es_de_wikitext(txt, pos_de.get(w, ""))
                if es:
                    res[w] = es
    # respaldo: MyMemory, con un tope diario de caracteres
    for w in falta:
        if w in res or w not in textos or mymemory_chars[0] > 4500:
            continue
        mymemory_chars[0] += len(w)
        d = get_json("https://api.mymemory.translated.net/get?" + urllib.parse.urlencode({"q": w, "langpair": "en|es"})) or {}
        t = ((d.get("responseData") or {}).get("translatedText") or "").strip()
        if t and t.lower() != w and not re.search(r"MYMEMORY|QUERY", t, re.I):
            res[w] = t
    for w in falta:
        if w in textos:
            cache["es"][w] = [res.get(w, ""), HOY]
    return res


class Ejemplos:
    """Busca ejemplos entre las frases ya descargadas (índice por palabra, sin consultas a internet)."""

    def __init__(self, frases):
        self.idx = {}
        for f in frases:
            for w in set(re.findall(r"[a-z]+", f["text"].lower())):
                self.idx.setdefault(w, []).append(f)

    def de(self, word):
        hits = self.idx.get(word.lower())
        if hits:
            h = random.choice(hits)
            return h["text"], h["es"]
        return "", ""


def palabras_de(seeds, conocidas, ejemplos, tomadas):
    def buscar(seed):
        q = urllib.parse.urlencode({"ml": seed, "topics": ",".join(seeds[:5]), "md": "dpfr", "ipa": "1", "max": "150"})
        return get_json("https://api.datamuse.com/words?" + q) or []

    pool = {}
    for lista in en_paralelo(buscar, seeds, hilos=6):
        for r in lista:
            w, tags = r.get("word", ""), r.get("tags", [])
            if not re.fullmatch(r"[a-z]{3,14}", w) or w in conocidas or w in tomadas or "prop" in tags or w in pool:
                continue
            f = next((float(t[2:]) for t in tags if t.startswith("f:")), 0.0)
            if 0.6 <= f <= 400 and any(t in ("n", "v", "adj", "adv") for t in tags):
                info = info_de_datamuse(r)
                if info["def"]:
                    info["f"] = f
                    pool[w] = info
    # las más útiles primero (más frecuentes), con algo de variedad
    cands = sorted(pool.values(), key=lambda c: -c["f"] * random.uniform(0.6, 1.4))[:130]
    words = [c["word"] for c in cands]
    trad = traducir_todas(cands)
    audios = voces_reales(words)
    out = []
    for c in cands:
        c.pop("f", None)
        c["es"] = trad.get(c["word"], "")
        if not c["es"]:
            continue
        c["audio"] = audios.get(c["word"], "")
        c["example"], c["es_example"] = ejemplos.de(c["word"])
        out.append(c)
        if len(out) >= 100:
            break
    for c in out:
        tomadas.add(c["word"])
    out.sort(key=lambda r: (0 if r["audio"] else 1, 0 if r["example"] else 1))
    return out


# ---------- guardar sin perder datos buenos ----------
def leer(nombre, defecto):
    try:
        return json.loads((DATA / nombre).read_text(encoding="utf-8"))
    except Exception:
        return defecto


def escribir(nombre, datos):
    (DATA / nombre).write_text(json.dumps(datos, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def main():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    vocab = sorted(set(re.findall(r'\["([a-z][a-z\']+)","/', html)))
    log(f"Palabras de la app: {len(vocab)}")
    cargar_cache()
    log(f"Memoria: {sum(1 for v in cache['es'].values() if v[0])} traducciones, "
        f"{sum(1 for v in cache['audio'].values() if v[0])} voces")

    # 1) banco general
    t = time.monotonic()
    banco = banco_general()
    if len(banco) >= 200 or not (DATA / "banco.json").exists():
        escribir("banco.json", banco)
    else:
        banco = leer("banco.json", [])
    log(f"Banco general: {len(banco)} frases ({time.monotonic() - t:.0f} s)")

    # 2) frases por tema (se parte de los datos anteriores: un tema que falle conserva lo de antes)
    t = time.monotonic()
    frases = leer("frases.json", {})
    for topic, seeds in TOPIC_SEEDS.items():
        if not queda_tiempo(10 * 60):
            log("  (sin tiempo: se conservan las frases anteriores de los temas que faltan)")
            break
        nuevas = frases_de(seeds[:6])
        if len(nuevas) >= 20 or topic not in frases:
            frases[topic] = nuevas
        log(f"Frases {topic}: {len(frases[topic])}")
    escribir("frases.json", frases)
    log(f"  frases listas ({time.monotonic() - t:.0f} s)")

    # 3) palabras por tema, guardando el avance tema por tema
    ejemplos = Ejemplos(banco + [x for l in frases.values() for x in l])
    palabras = leer("palabras.json", {})
    tomadas = set()
    for topic, seeds in TOPIC_SEEDS.items():
        if not queda_tiempo(3 * 60):
            log("  (sin tiempo: se conservan las palabras anteriores de los temas que faltan)")
            break
        t = time.monotonic()
        nuevas = palabras_de(seeds, set(vocab), ejemplos, tomadas)
        if len(nuevas) >= 20 or topic not in palabras:
            palabras[topic] = nuevas
        log(f"Palabras {topic}: {len(palabras[topic])} (con voz real: "
            f"{sum(1 for c in palabras[topic] if c['audio'])}) ({time.monotonic() - t:.0f} s)")
        escribir("palabras.json", palabras)
        guardar_cache()

    # 4) voces para las palabras escritas a mano y las descargadas
    pron = {w: {"audio": u} for w, u in voces_reales(vocab).items()}
    for lst in palabras.values():
        for r in lst:
            if r.get("audio"):
                pron[r["word"]] = {"audio": r["audio"], "ipa": r.get("ipa", "")}
    if len(pron) >= 10 or not (DATA / "pronunciacion.json").exists():
        escribir("pronunciacion.json", pron)
    guardar_cache()
    log("Listo:", sum(map(len, frases.values())), "frases,", sum(map(len, palabras.values())), "palabras,",
        len(pron), "pronunciaciones,", len(banco), "en el banco general", f"— total {time.monotonic() - INICIO:.0f} s")


if __name__ == "__main__":
    main()
