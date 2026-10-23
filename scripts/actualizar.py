
"""Descarga frases con audio (Tatoeba), palabras por tema (Datamuse) y
pronunciaciones grabadas (Free Dictionary API) y las guarda en data/.
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
            print("  HTTP", e.code, url)
        except Exception as e:  # red inestable: reintentar
            print("  error", e, url)
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


# ---------- diccionario ----------
dict_cache = {}


def lookup(word):
    if word in dict_cache:
        return dict_cache[word]
    d = get_json("https://api.dictionaryapi.dev/api/v2/entries/en/" + urllib.parse.quote(word))
    time.sleep(0.25)
    if not isinstance(d, list) or not d:
        dict_cache[word] = None
        return None
    ipa = d[0].get("phonetic") or next((p.get("text") for p in d[0].get("phonetics", []) if p.get("text")), "")
    urls = [p["audio"] if not p["audio"].startswith("//") else "https:" + p["audio"]
            for e in d for p in e.get("phonetics", []) if p.get("audio")]
    audio = next((u for u in urls if re.search(r"-us\.mp3", u, re.I)), urls[0] if urls else "")
    definition = example = pos = ""
    for e in d:
        for m in e.get("meanings", []):
            for df in m.get("definitions", []):
                if not definition and df.get("definition"):
                    definition, pos = df["definition"], m.get("partOfSpeech", "")
                if not example and df.get("example") and len(df["example"].split()) > 3:
                    example = df["example"]
    res = {"word": word, "ipa": ipa, "audio": audio, "def": definition, "pos": pos, "example": example}
    dict_cache[word] = res
    return res


# ---------- banco de palabras ----------
def palabras_de(seeds, conocidas):
    pool = set()
    for seed in seeds:
        q = urllib.parse.urlencode({"ml": seed, "topics": ",".join(seeds), "md": "pf", "max": "120"})
        for r in get_json("https://api.datamuse.com/words?" + q) or []:
            w, tags = r.get("word", ""), r.get("tags", [])
            if not re.fullmatch(r"[a-z]{4,14}", w) or w in conocidas or w in seeds or "prop" in tags:
                continue
            f = next((float(t[2:]) for t in tags if t.startswith("f:")), 0.0)
            if 0.8 <= f <= 150 and any(t in ("n", "v", "adj") for t in tags):
                pool.add(w)
        time.sleep(0.3)
    cands = list(pool)
    random.shuffle(cands)
    out = []
    for w in cands[:70]:
        r = lookup(w)
        if r and r["def"]:
            out.append(r)
    out.sort(key=lambda r: 0 if r["audio"] else 1)
    return out[:50]


def main():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    vocab = sorted(set(re.findall(r'\["([a-z][a-z\']+)","/', html)))
    print(f"Palabras de la app: {len(vocab)}")

    frases, palabras = {}, {}
    for topic, seeds in TOPIC_SEEDS.items():
        print("Tema:", topic)
        frases[topic] = frases_de(seeds)
        palabras[topic] = palabras_de(seeds, set(vocab))
        print(f"  {len(frases[topic])} frases, {len(palabras[topic])} palabras")

    pron = {}
    for w in vocab:
        r = lookup(w)
        if r and r["audio"]:
            pron[w] = {"audio": r["audio"], "ipa": r["ipa"]}
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
