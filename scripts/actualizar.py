"""Descarga frases con audio (Tatoeba), palabras por tema (Datamuse) y
pronunciaciones grabadas por personas (Wikimedia Commons) y las guarda en data/.
La app las lee desde el mismo sitio, así el navegador no bloquea nada.
Lo ejecuta GitHub Actions; no hace falta correrlo a mano."""
import json, random, re, time, urllib.parse, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
UA = {"User-Agent": "ingles-para-vila/1.0 (proyecto personal de práctica de inglés)"}

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


def get_json(url, tries=3):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code == 429:
                time.sleep(5 * (i + 1))
                continue
            print("  HTTP", e.code, url, flush=True)
            if e.code >= 500 and i >= 1:
                return None
        except Exception as e:  # red inestable: reintentar
            print("  error", e, url, flush=True)
        time.sleep(2 * (i + 1))
    return None


def flat(x):
    if isinstance(x, list):
        for y in x:
            yield from flat(y)
    elif isinstance(x, dict):
        yield x


# ---------- frases con audio ----------
def frases_de(seeds):
    out, seen = [], set()
    for seed in seeds:
        q = urllib.parse.urlencode({
            "lang": "eng", "q": seed, "has_audio": "yes", "word_count": "4-13",
            "trans:lang": "spa", "include": "audios", "sort": "random", "limit": "40",
        })
        d = get_json("https://api.tatoeba.org/v1/sentences?" + q) or {}
        for s in d.get("data", []):
            if s.get("id") in seen or not s.get("audios"):
                continue
            a = s["audios"][0]
            es = next((t.get("text") for t in flat(s.get("translations", [])) if t.get("lang") == "spa"), "")
            if not es:
                continue
            seen.add(s["id"])
            out.append({
                "id": s["id"], "text": s.get("text", ""), "es": es,
                "audio": a.get("download_url") or f"https://audio.tatoeba.org/sentences/eng/{s['id']}.mp3",
                "author": a.get("author") or "", "license": a.get("license") or a.get("licence") or "",
                "attr": a.get("attribution_url") or "",
            })
        time.sleep(0.5)
    random.shuffle(out)
    return out[:120]


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


def banco_general(rondas=40):
    """Frases variadas con audio y traducción, para buscar temas que Vila escriba a mano."""
    out, seen = [], set()
    for _ in range(rondas):
        q = urllib.parse.urlencode({"lang": "eng", "has_audio": "yes", "word_count": "4-12", "trans:lang": "spa",
                                    "include": "audios", "sort": "random", "limit": "100"})
        for s in (get_json("https://api.tatoeba.org/v1/sentences?" + q) or {}).get("data", []):
            f = a_frase(s)
            if f and f["id"] not in seen:
                seen.add(f["id"])
                out.append(f)
        time.sleep(0.5)
    return out


# ---------- palabras: datos, voz real y ejemplos ----------
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
    return {"word": r.get("word", ""), "ipa": "/" + ipa + "/" if ipa else "", "def": definition, "pos": pos,
            "example": "", "es_example": "", "audio": ""}


def voces_reales(words):
    """Grabaciones de Wikimedia Commons (archivos En-us-<palabra>.ogg), en mp3 cuando existe."""
    out = {}
    words = [w for w in words if re.fullmatch(r"[a-z]+", w)]
    for i in range(0, len(words), 40):
        lote = words[i:i + 40]
        titles = "|".join("File:En-us-" + w + ".ogg" for w in lote)
        q = urllib.parse.urlencode({"action": "query", "titles": titles, "prop": "videoinfo",
                                    "viprop": "url|derivatives", "format": "json", "formatversion": "2"})
        d = get_json("https://commons.wikimedia.org/w/api.php?" + q) or {}
        for page in d.get("query", {}).get("pages", []):
            if page.get("missing") or not page.get("videoinfo"):
                continue
            m = re.match(r"File:En-us-(.+)\.ogg$", page.get("title", ""), re.I)
            if not m:
                continue
            vi = page["videoinfo"][0]
            mp3 = next((x.get("src") for x in vi.get("derivatives", []) if "mpeg" in (x.get("type") or "") or x.get("transcodekey") == "mp3"), "")
            url = mp3 or vi.get("url", "")
            if url.startswith("//"):
                url = "https:" + url
            if url:
                out[m.group(1).lower()] = url
        time.sleep(0.5)
    return out


def ejemplo_de(word):
    q = urllib.parse.urlencode({"lang": "eng", "q": word, "word_count": "4-12", "trans:lang": "spa",
                                "sort": "random", "limit": "10"})
    d = get_json("https://api.tatoeba.org/v1/sentences?" + q) or {}
    time.sleep(0.3)
    for s in d.get("data", []):
        text = s.get("text", "")
        if not re.search(r"\b" + re.escape(word) + r"\b", text, re.I):
            continue
        es = next((t.get("text") for t in flat(s.get("translations", [])) if t.get("lang") == "spa"), "")
        if es:
            return text, es
    return "", ""


# ---------- traducción al español (Wiktionary, con respaldo de MyMemory) ----------
trad_cache = {}
mymemory_chars = [0]


def traducir(word):
    if word in trad_cache:
        return trad_cache[word]
    es = []
    for page in (word, word + "/translations"):
        q = urllib.parse.urlencode({"action": "parse", "page": page, "prop": "wikitext", "format": "json",
                                    "formatversion": "2", "redirects": "1"})
        d = get_json("https://en.wiktionary.org/w/api.php?" + q) or {}
        time.sleep(0.15)
        text = (d.get("parse") or {}).get("wikitext") or ""
        if not text:
            continue
        # solo la sección en inglés
        m = re.search(r"==English==(.*?)(\n==[^=]|\Z)", text, re.S)
        text = m.group(1) if m else text
        for t in re.findall(r"\{\{tt?\+?\|es\|([^|}]+)", text):
            t = t.strip()
            if t and t not in es:
                es.append(t)
            if len(es) >= 2:
                break
        if es or "translation subpage" not in text:
            break
    res = ", ".join(es)
    if not res and mymemory_chars[0] < 4500:
        mymemory_chars[0] += len(word)
        d = get_json("https://api.mymemory.translated.net/get?" + urllib.parse.urlencode({"q": word, "langpair": "en|es"})) or {}
        t = ((d.get("responseData") or {}).get("translatedText") or "").strip()
        if t and t.lower() != word and not re.search(r"MYMEMORY|QUERY", t, re.I):
            res = t
        time.sleep(0.3)
    trad_cache[word] = res
    return res


def buscar_ejemplo(word, pool):
    rx = re.compile(r"\b" + re.escape(word) + r"\b", re.I)
    hits = [x for x in pool if rx.search(x["text"])]
    if hits:
        h = random.choice(hits)
        return h["text"], h["es"]
    return ejemplo_de(word)


# ---------- banco de palabras ----------
def palabras_de(seeds, conocidas, pool_frases, tomadas):
    pool = {}
    for seed in seeds:
        q = urllib.parse.urlencode({"ml": seed, "topics": ",".join(seeds[:5]), "md": "dpfr", "ipa": "1", "max": "150"})
        for r in get_json("https://api.datamuse.com/words?" + q) or []:
            w, tags = r.get("word", ""), r.get("tags", [])
            if not re.fullmatch(r"[a-z]{3,14}", w) or w in conocidas or w in tomadas or "prop" in tags or w in pool:
                continue
            f = next((float(t[2:]) for t in tags if t.startswith("f:")), 0.0)
            if 0.6 <= f <= 400 and any(t in ("n", "v", "adj", "adv") for t in tags):
                info = info_de_datamuse(r)
                if info["def"]:
                    info["f"] = f
                    pool[w] = info
        time.sleep(0.3)
    # las más útiles primero (más frecuentes), con algo de variedad
    cands = sorted(pool.values(), key=lambda c: -c["f"] * random.uniform(0.6, 1.4))[:110]
    audios = voces_reales([c["word"] for c in cands])
    out = []
    for c in cands:
        c.pop("f", None)
        c["audio"] = audios.get(c["word"], "")
        c["es"] = traducir(c["word"])
        if not c["es"]:
            continue
        c["example"], c["es_example"] = buscar_ejemplo(c["word"], pool_frases)
        out.append(c)
        if len(out) >= 100:
            break
    for c in out:
        tomadas.add(c["word"])
    out.sort(key=lambda r: (0 if r["audio"] else 1, 0 if r["example"] else 1))
    return out


def main():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    vocab = sorted(set(re.findall(r'\["([a-z][a-z\']+)","/', html)))
    print(f"Palabras de la app: {len(vocab)}", flush=True)

    # reutilizar traducciones de la semana anterior para ir más rápido
    try:
        for lst in json.loads((DATA / "palabras.json").read_text(encoding="utf-8")).values():
            for c in lst:
                if c.get("es"):
                    trad_cache[c["word"]] = c["es"]
    except Exception:
        pass

    print("Banco general…", flush=True)
    banco = banco_general()
    if len(banco) >= 200 or not (DATA / "banco.json").exists():
        (DATA / "banco.json").write_text(json.dumps(banco, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print("  banco:", len(banco), "frases", flush=True)

    frases = {}
    for topic, seeds in TOPIC_SEEDS.items():
        frases[topic] = frases_de(seeds[:6])
        print(f"Frases {topic}: {len(frases[topic])}", flush=True)
    pool_frases = banco + [x for l in frases.values() for x in l]

    palabras, tomadas = {}, set()
    for topic, seeds in TOPIC_SEEDS.items():
        palabras[topic] = palabras_de(seeds, set(vocab), pool_frases, tomadas)
        print(f"Palabras {topic}: {len(palabras[topic])} (con voz real: {sum(1 for c in palabras[topic] if c['audio'])})", flush=True)

    pron = {w: {"audio": u} for w, u in voces_reales(vocab).items()}
    for lst in palabras.values():
        for r in lst:
            if r["audio"]:
                pron[r["word"]] = {"audio": r["audio"], "ipa": r["ipa"]}

    # No reemplazar datos buenos con una descarga vacía (si una fuente estuvo caída)
    def guardar(nombre, nuevo, minimo):
        p = DATA / nombre
        if sum(len(v) for v in nuevo.values()) < minimo and p.exists():
            print("  ", nombre, "vino casi vacío; se conserva la versión anterior")
            return
        p.write_text(json.dumps(nuevo, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    guardar("frases.json", frases, 40)
    guardar("palabras.json", palabras, 80)
    guardar("pronunciacion.json", pron, 10)
    print("Listo:", sum(map(len, frases.values())), "frases,", sum(map(len, palabras.values())), "palabras,", len(pron), "pronunciaciones", flush=True)


if __name__ == "__main__":
    main()
