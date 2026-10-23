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
    "Crimen y juicios": ["crime", "court", "police", "prison", "trial"],
    "Abogados": ["lawyer", "law", "judge", "contract", "court"],
    "Laboratorio y química": ["chemistry", "laboratory", "chemical", "science", "experiment"],
    "Flores y jardín": ["flower", "garden", "plant", "bloom", "seed"],
    "Libros de suspenso": ["book", "mystery", "secret", "story", "detective"],
    "Terror": ["ghost", "fear", "dark", "scary", "nightmare"],
    "Detectives": ["detective", "clue", "murder", "investigation", "suspect"],
    "Viajes": ["travel", "airport", "hotel", "trip", "ticket"],
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


# ---------- banco de palabras ----------
def palabras_de(seeds, conocidas):
    pool = {}
    for seed in seeds:
        q = urllib.parse.urlencode({"ml": seed, "topics": ",".join(seeds), "md": "dpfr", "ipa": "1", "max": "120"})
        for r in get_json("https://api.datamuse.com/words?" + q) or []:
            w, tags = r.get("word", ""), r.get("tags", [])
            if not re.fullmatch(r"[a-z]{4,14}", w) or w in conocidas or w in seeds or "prop" in tags or w in pool:
                continue
            f = next((float(t[2:]) for t in tags if t.startswith("f:")), 0.0)
            if 0.8 <= f <= 150 and any(t in ("n", "v", "adj") for t in tags):
                info = info_de_datamuse(r)
                if info["def"]:
                    pool[w] = info
        time.sleep(0.3)
    cands = list(pool.values())
    random.shuffle(cands)
    cands = cands[:60]
    audios = voces_reales([c["word"] for c in cands])
    for c in cands:
        c["audio"] = audios.get(c["word"], "")
        c["example"], c["es_example"] = ejemplo_de(c["word"])
    cands.sort(key=lambda r: (0 if r["audio"] else 1, 0 if r["example"] else 1))
    return cands[:50]


def main():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    vocab = sorted(set(re.findall(r'\["([a-z][a-z\']+)","/', html)))
    print(f"Palabras de la app: {len(vocab)}")

    frases, palabras = {}, {}
    for topic, seeds in TOPIC_SEEDS.items():
        print("Tema:", topic, flush=True)
        frases[topic] = frases_de(seeds)
        palabras[topic] = palabras_de(seeds, set(vocab))
        print(f"  {len(frases[topic])} frases, {len(palabras[topic])} palabras")

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
    guardar("palabras.json", palabras, 40)
    guardar("pronunciacion.json", pron, 10)
    print("Listo:", sum(map(len, frases.values())), "frases,", sum(map(len, palabras.values())), "palabras,", len(pron), "pronunciaciones")


if __name__ == "__main__":
    main()
