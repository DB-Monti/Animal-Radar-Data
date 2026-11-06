"""
AnimalRadar – data-ophaler voor heel Nederland (GBIF + iNaturalist)

Toont alle zeldzame en zeer zeldzame gewervelde dieren (vogels, zoogdieren,
reptielen, amfibieën) in Nederland.

Nederland is verdeeld in tegels van ca. 55 x 55 km. Per tegel:
  1. Tel per soort hoeveel waarnemingen er zijn (dit en vorig jaar).
  2. Soorten met weinig waarnemingen zijn in die omgeving bijzonder:
       <= ZEER_ZELDZAAM_MAX  -> "Zeer Zeldzaam"
       <= ZELDZAAM_MAX       -> "Zeldzaam"
     (zeldzaamheid is dus lokaal: een otter bij Zwolle telt, ook al is hij in
      Friesland algemener)
  3. Haal van die soorten de waarnemingen op, met Nederlandse naam.
  4. Soorten uit ALTIJD_TONEN komen er altijd bij.
  5. Aanvulling via iNaturalist (laatste INAT_DAGEN dagen; direct beschikbaar).
  6. Kwetsbare soorten krijgen afgevlakte coördinaten (~1 km).

Uitvoer (map data/):
  data/index.json         -> overzicht van alle tegels (grenzen, bestand, aantal)
  data/tile_rXcY.json     -> waarnemingen per tegel
  data/photos.json        -> één foto per soort (iNaturalist, alleen open licenties)
                             Wordt bewaard tussen runs; alleen nieuwe soorten worden opgezocht.
                             Verwijder dit bestand om alle foto's opnieuw op te halen.
De website laadt alleen de tegels rond de gekozen locatie / het kaartbeeld.
"""
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------------------
# Instellingen
# ---------------------------------------------------------------------------

# Nederland in tegels (rijen x kolommen). Tegels zonder data worden overgeslagen.
LAT_START, LAT_END, LAT_STEP = 50.70, 53.70, 0.50   # ~55 km per rij
LNG_START, LNG_END, LNG_STEP = 3.30, 7.30, 0.80     # ~55 km per kolom

OUTPUT_DIR = "data"

# Periode: vorig jaar + dit jaar
THIS_YEAR = datetime.now().year
YEARS = f"{THIS_YEAR - 1},{THIS_YEAR}"

# iNaturalist alleen voor recente waarnemingen (oudere staan al in GBIF)
INAT_DAGEN = 90
INAT_PLACE_NL = 7506  # iNaturalist place_id van Nederland

# Drempels (aantal waarnemingen per tegel in de periode)
ZEER_ZELDZAAM_MAX = 5
ZELDZAAM_MAX = 25

# Maximaal aantal waarnemingen per soort per tegel
MAX_PER_SOORT = 25

# Tijdsbewaking: na zoveel minuten worden geen nieuwe tegels/foto's meer opgehaald
# en wordt opgeslagen wat er is (tegels die niet aan de beurt kwamen houden hun
# vorige bestand). Houd dit ruim onder de timeout-minutes van de workflow.
MAX_LOOPTIJD_MIN = 100
FOTO_LOOPTIJD_MIN = 120

# Aantal gelijktijdige verzoeken aan GBIF (soortinformatie en namen)
GBIF_THREADS = 8

# Diergroepen die meedoen (wetenschappelijke naam -> GBIF-key als terugval)
DIERGROEPEN = {"Aves": 212, "Mammalia": 359, "Reptilia": 358, "Amphibia": 131}

# Soorten die altijd getoond worden, met vaste naam/zeldzaamheid
ALTIJD_TONEN = [
    {"scientificName": "Canis lupus", "name": "Wolf", "emoji": "🐺", "size": "wolf", "sizeLabel": "Groot roofdier (Topfauna)", "rarity": "Zeer Zeldzaam"},
    {"scientificName": "Lutra lutra", "name": "Otter", "emoji": "🦦", "size": "medium", "rarity": "Zeer Zeldzaam"},
    {"scientificName": "Castor fiber", "name": "Bever", "emoji": "🦫", "size": "medium", "rarity": "Vrij Zeldzaam"},
    {"scientificName": "Haliaeetus albicilla", "name": "Zeearend", "emoji": "🦅", "size": "birds", "rarity": "Zeer Zeldzaam"},
    {"scientificName": "Pandion haliaetus", "name": "Visarend", "emoji": "🦅", "size": "birds", "rarity": "Zeer Zeldzaam"},
    {"scientificName": "Bubo bubo", "name": "Oehoe", "emoji": "🦉", "size": "birds", "rarity": "Zeer Zeldzaam"},
    {"scientificName": "Ciconia nigra", "name": "Zwarte ooievaar", "emoji": "🦩", "size": "birds", "rarity": "Zeer Zeldzaam"},
    {"scientificName": "Grus grus", "name": "Kraanvogel", "emoji": "🦩", "size": "birds", "rarity": "Vrij Zeldzaam"},
    {"scientificName": "Falco peregrinus", "name": "Slechtvalk", "emoji": "🦅", "size": "birds", "rarity": "Vrij Zeldzaam"},
    {"scientificName": "Corvus corax", "name": "Raaf", "emoji": "🐦‍⬛", "size": "birds", "rarity": "Zeldzaam"},
    {"scientificName": "Cervus elaphus", "name": "Edelhert", "emoji": "🦌", "size": "large", "rarity": "Vrij Zeldzaam"},
    {"scientificName": "Sus scrofa", "name": "Wild zwijn", "emoji": "🐗", "size": "large", "rarity": "Regelmatig"},
]

# Kwetsbare soorten: coördinaten altijd afvlakken (naast alle 'Zeer Zeldzaam')
KWETSBAAR = {
    "Canis lupus", "Lutra lutra", "Bubo bubo", "Haliaeetus albicilla", "Pandion haliaetus",
    "Ciconia nigra", "Falco peregrinus", "Felis silvestris", "Vipera berus", "Coronella austriaca",
}

# Huisdieren / mensen / ontsnapte dieren die we nooit willen tonen
UITSLUITEN = {
    "Homo sapiens", "Canis familiaris", "Felis catus", "Bos taurus", "Ovis aries", "Capra hircus",
    "Equus caballus", "Sus domesticus", "Oryctolagus cuniculus domesticus", "Cavia porcellus",
}
UITSLUITEN_WOORDEN = ("familiaris", "domesticus", "domestica", "catus")

BASIS_FILTERS = {
    "country": "NL",
    "hasCoordinate": "true",
    "hasGeospatialIssue": "false",
    "occurrenceStatus": "PRESENT",
    "year": YEARS,
    "basisOfRecord": ["HUMAN_OBSERVATION", "MACHINE_OBSERVATION", "OBSERVATION"],
}

# ---------------------------------------------------------------------------
# Tegels
# ---------------------------------------------------------------------------


def make_tiles():
    tiles, r, lat = [], 0, LAT_START
    while lat < LAT_END - 1e-9:
        c, lng = 0, LNG_START
        while lng < LNG_END - 1e-9:
            tiles.append({
                "id": f"r{r}c{c}",
                "latMin": round(lat, 4), "latMax": round(lat + LAT_STEP, 4),
                "lngMin": round(lng, 4), "lngMax": round(lng + LNG_STEP, 4),
            })
            c, lng = c + 1, lng + LNG_STEP
        r, lat = r + 1, lat + LAT_STEP
    return tiles


def tile_filters(tile):
    f = dict(BASIS_FILTERS)
    f["decimalLatitude"] = f"{tile['latMin']},{tile['latMax']}"
    f["decimalLongitude"] = f"{tile['lngMin']},{tile['lngMax']}"
    return f


def tile_for_point(tiles, lat, lng):
    for t in tiles:
        if t["latMin"] <= lat < t["latMax"] and t["lngMin"] <= lng < t["lngMax"]:
            return t
    return None

# ---------------------------------------------------------------------------
# API-hulpfuncties
# ---------------------------------------------------------------------------

API = "https://api.gbif.org/v1/"
INAT_API = "https://api.inaturalist.org/v1/"
_cache = {}


def http_get(url, pause=0.05, retries=3):
    if url in _cache:
        return _cache[url]
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "AnimalRadar/3.0 (animalradar.nl)"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            _cache[url] = data
            time.sleep(pause)  # vriendelijk voor de API
            return data
        except Exception as e:
            last_err = e
            time.sleep(2 * (attempt + 1))
    raise last_err


def gbif_get(path, params=None, retries=3):
    url = API + path
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    return http_get(url, pause=0.05, retries=retries)


def inat_get(path, params=None, retries=3):
    url = INAT_API + path
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    return http_get(url, pause=1.0, retries=retries)  # iNaturalist vraagt max ~1 verzoek/sec


def match_key(name, rank=None):
    params = {"name": name, "strict": "true"}
    if rank:
        params["rank"] = rank
    d = gbif_get("species/match", params)
    if d.get("matchType") == "NONE":
        return None
    return d.get("acceptedUsageKey") or d.get("usageKey")


def species_info(key):
    return gbif_get(f"species/{key}")


def dutch_name(key, fallback):
    try:
        d = gbif_get(f"species/{key}/vernacularNames", {"limit": 200})
        for v in d.get("results", []):
            if v.get("language") in ("nld", "nl", "dut"):
                n = str(v.get("vernacularName", "")).strip()
                if n:
                    return n[:1].upper() + n[1:]
    except Exception:
        pass
    return fallback


def species_counts(tile, class_keys):
    """Aantal waarnemingen per soort in een tegel (via GBIF facets)."""
    counts, offset, page = {}, 0, 500
    while True:
        params = tile_filters(tile)
        params.update({"classKey": class_keys, "limit": 0, "facet": "speciesKey",
                       "facetLimit": page, "facetOffset": offset})
        d = gbif_get("occurrence/search", params)
        facets = d.get("facets") or []
        rows = facets[0].get("counts", []) if facets else []
        for r in rows:
            counts[int(r["name"])] = int(r["count"])
        if len(rows) < page:
            break
        offset += page
    return counts


def fetch_occurrences(tile, taxon_key, limit):
    params = tile_filters(tile)
    params.update({"taxonKey": taxon_key, "limit": limit})
    return gbif_get("occurrence/search", params).get("results", [])


def fetch_occurrences_batch(tile, keys):
    """Alle waarnemingen van veel (zeldzame) soorten tegelijk, gegroepeerd per soort.
    Zeldzame soorten hebben per definitie weinig waarnemingen, dus een paar
    verzoeken met 300 resultaten volstaan in plaats van één verzoek per soort."""
    per_key = {k: [] for k in keys}
    keys = list(keys)
    for i in range(0, len(keys), 100):
        chunk = keys[i:i + 100]
        offset = 0
        while True:
            params = tile_filters(tile)
            params.update({"taxonKey": chunk, "limit": 300, "offset": offset})
            d = gbif_get("occurrence/search", params)
            for occ in d.get("results", []):
                k = occ.get("speciesKey")
                if k in per_key and len(per_key[k]) < MAX_PER_SOORT:
                    per_key[k].append(occ)
            offset += 300
            if d.get("endOfRecords", True) or offset >= 20000:
                break
    return per_key

# ---------------------------------------------------------------------------
# Indeling: emoji, categorie en label
# ---------------------------------------------------------------------------

VOGEL_EMOJI = {
    "Accipitriformes": "🦅", "Falconiformes": "🦅", "Strigiformes": "🦉", "Anseriformes": "🦆",
    "Gruiformes": "🦩", "Ciconiiformes": "🦩", "Pelecaniformes": "🦩", "Phoenicopteriformes": "🦩",
    "Columbiformes": "🕊️", "Galliformes": "🐓", "Psittaciformes": "🦜",
}
SLANG_FAMILIES = {"Colubridae", "Viperidae", "Natricidae"}


def classify(info):
    cls, order, family = info.get("class"), info.get("order"), info.get("family")
    if cls == "Aves":
        return {"emoji": VOGEL_EMOJI.get(order, "🐦"), "size": "birds", "sizeLabel": "Bijzondere vogel"}
    if cls == "Mammalia":
        if family in ("Cervidae", "Suidae", "Bovidae") or order in ("Artiodactyla", "Cetartiodactyla"):
            emoji = "🐗" if family == "Suidae" else "🦌"
            return {"emoji": emoji, "size": "large", "sizeLabel": "Groot wild"}
        if order == "Chiroptera":
            return {"emoji": "🦇", "size": "medium", "sizeLabel": "Vleermuis"}
        if order == "Eulipotyphla":
            return {"emoji": "🦔", "size": "medium", "sizeLabel": "Klein zoogdier"}
        if order == "Rodentia":
            return {"emoji": "🐭", "size": "medium", "sizeLabel": "Klein zoogdier"}
        if order == "Lagomorpha":
            return {"emoji": "🐇", "size": "medium", "sizeLabel": "Middelgroot zoogdier"}
        if family == "Canidae":
            return {"emoji": "🦊", "size": "medium", "sizeLabel": "Middelgroot zoogdier"}
        if family == "Felidae":
            return {"emoji": "🐈", "size": "medium", "sizeLabel": "Middelgroot zoogdier"}
        if family == "Mustelidae":
            return {"emoji": "🦡", "size": "medium", "sizeLabel": "Middelgroot zoogdier"}
        if family in ("Phocidae", "Delphinidae", "Phocoenidae"):
            return {"emoji": "🦭" if family == "Phocidae" else "🐬", "size": "medium", "sizeLabel": "Zeezoogdier"}
        return {"emoji": "🐾", "size": "medium", "sizeLabel": "Zoogdier"}
    if cls == "Reptilia":
        if family in SLANG_FAMILIES:
            emoji = "🐍"
        elif order == "Testudines":
            emoji = "🐢"
        else:
            emoji = "🦎"
        return {"emoji": emoji, "size": "herps", "sizeLabel": "Reptiel"}
    if cls == "Amphibia":
        emoji = "🦎" if order == "Caudata" else "🐸"
        return {"emoji": emoji, "size": "herps", "sizeLabel": "Amfibie"}
    return {"emoji": "🐾", "size": "medium", "sizeLabel": "Natuurwaarneming"}


def is_excluded(name):
    n = (name or "").strip()
    if not n:
        return False
    if any(n.startswith(x) for x in UITSLUITEN):
        return True
    return any(w in n.lower().split() for w in UITSLUITEN_WOORDEN)


def is_protected(meta):
    return meta["rarity"] == "Zeer Zeldzaam" or meta["scientificName"] in KWETSBAAR

# ---------------------------------------------------------------------------
# Records bouwen (compact: de website vult badge/kleur zelf in)
# ---------------------------------------------------------------------------


def make_record(rid, meta, lat, lng, place, date, protected, source, url, desc):
    rec = {
        "id": rid,
        "name": meta["name"],
        "sci": meta["scientificName"],
        "emoji": meta["emoji"],
        "size": meta["size"],
        "label": meta["sizeLabel"],
        "lat": lat,
        "lng": lng,
        "place": place,
        "seenAt": date,
        "rarity": meta["rarity"],
        "src": source,
    }
    if protected:
        rec["protected"] = True
    if url:
        rec["url"] = url
    if desc:
        rec["desc"] = desc[:500]
    return rec


def build_records(occurrences, meta, inat_ids_in_gbif):
    out = []
    protected = is_protected(meta)
    for occ in occurrences:
        if is_excluded(occ.get("scientificName")):
            continue
        if str(occ.get("institutionCode", "")).lower() == "inaturalist" and occ.get("catalogNumber"):
            inat_ids_in_gbif.add(str(occ.get("catalogNumber")))
        lat, lng = occ.get("decimalLatitude"), occ.get("decimalLongitude")
        date = (occ.get("eventDate") or "")[:10]
        if lat is None or lng is None or not date:
            continue
        if protected:
            lat, lng = round(float(lat), 2), round(float(lng), 2)
            place = occ.get("municipality") or occ.get("county") or occ.get("stateProvince") or "Nederland"
        else:
            lat, lng = round(float(lat), 4), round(float(lng), 4)
            place = occ.get("locality") or occ.get("municipality") or occ.get("stateProvince") or "Nederland"
        key = occ.get("key")
        out.append(make_record(
            key, meta, lat, lng, place, date, protected, "GBIF",
            f"https://www.gbif.org/occurrence/{key}" if key else "",
            occ.get("occurrenceRemarks") or occ.get("eventRemarks") or "",
        ))
    return out


def fetch_inat_observations(tile):
    """Recente, door de community bevestigde, wilde waarnemingen in een tegel."""
    d1 = (datetime.now(timezone.utc) - timedelta(days=INAT_DAGEN)).strftime("%Y-%m-%d")
    results, page = [], 1
    while page <= 10:
        d = inat_get("observations", {
            "place_id": INAT_PLACE_NL,
            "swlat": tile["latMin"], "swlng": tile["lngMin"],
            "nelat": tile["latMax"], "nelng": tile["lngMax"],
            "iconic_taxa": "Aves,Mammalia,Reptilia,Amphibia",
            "quality_grade": "research",
            "captive": "false",
            "d1": d1,
            "order_by": "observed_on", "order": "desc",
            "locale": "nl",
            "per_page": 200, "page": page,
        })
        batch = d.get("results", [])
        results.extend(batch)
        if len(batch) < 200 or len(results) >= d.get("total_results", 0):
            break
        page += 1
    return results


def build_inat_record(obs, meta):
    coords = (obs.get("geojson") or {}).get("coordinates")
    if coords and len(coords) == 2:
        lng, lat = coords
    elif obs.get("location"):
        lat, lng = [float(x) for x in obs["location"].split(",")]
    else:
        return None
    date = (obs.get("observed_on") or "")[:10]
    if not date:
        return None
    protected = is_protected(meta) or bool(obs.get("obscured"))
    parts = [p.strip() for p in str(obs.get("place_guess") or "").split(",") if p.strip()]
    if protected:
        lat, lng = round(float(lat), 2), round(float(lng), 2)
        place = parts[-2] if len(parts) >= 2 else "Nederland"
    else:
        lat, lng = round(float(lat), 4), round(float(lng), 4)
        place = ", ".join(parts[:2]) if parts else "Nederland"
    return make_record(
        f"inat-{obs.get('id')}", meta, lat, lng, place, date, protected, "iNaturalist",
        f"https://www.inaturalist.org/observations/{obs.get('id')}",
        (obs.get("description") or "").strip(),
    )


def handmatige_meldingen():
    """Handmatig ingevoerde, geverifieerde meldingen (bijv. uit BIJ12-rapportages of nieuws)."""
    wolf = {"name": "Wolf", "scientificName": "Canis lupus", "emoji": "🐺", "size": "wolf",
            "sizeLabel": "Groot roofdier (Topfauna)", "rarity": "Zeer Zeldzaam"}
    return [
        make_record("hasselt-wolf-2026-10", wolf, 52.59, 6.10, "Hasselt (Overijssel)", "2026-10-22",
                    True, "Handmatig (geverifieerd)", "",
                    "Geverifieerde melding van een wolf in de regio Hasselt / Zwarte Waterland."),
    ]

# ---------------------------------------------------------------------------
# Eén tegel verwerken
# ---------------------------------------------------------------------------


def process_tile(tile, class_keys, vaste_soorten, stats):
    records, seen = [], set()
    meta_by_name, meta_by_key = {}, {}
    inat_ids_in_gbif = set()

    def add(recs):
        for item in recs:
            sig = f"{item['name']}_{item['lat']}_{item['lng']}_{item['seenAt']}"
            if sig not in seen:
                seen.add(sig)
                records.append(item)

    def remember(key, meta):
        meta_by_name[meta["scientificName"]] = meta
        meta_by_key[key] = meta

    # Tellen per soort; lege tegel (zee/buitenland) -> overslaan
    try:
        counts = species_counts(tile, class_keys)
    except Exception as e:
        print(f"  Fout bij tellen: {e}")
        return []
    if not counts:
        return []

    # Vaste soorten
    for key, meta in vaste_soorten.items():
        remember(key, meta)
        if key not in counts:
            continue
        try:
            add(build_records(fetch_occurrences(tile, key, MAX_PER_SOORT), meta, inat_ids_in_gbif))
        except Exception as e:
            print(f"  Fout bij {meta['name']}: {e}")

    # Automatisch: soorten die in deze tegel weinig gezien worden
    rare = {k: c for k, c in counts.items() if c <= ZELDZAAM_MAX and k not in vaste_soorten}

    def prefetch(key):  # soortinformatie + Nederlandse naam (gecachet, ook over tegels heen)
        try:
            info = species_info(key)
            sci = info.get("canonicalName") or info.get("scientificName") or ""
            if not is_excluded(sci) and info.get("rank") in (None, "SPECIES"):
                dutch_name(key, sci)
        except Exception as e:
            print(f"  Fout bij soortinfo {key}: {e}")

    with ThreadPoolExecutor(GBIF_THREADS) as pool:
        list(pool.map(prefetch, rare))

    rare_meta = {}
    for key, count in rare.items():
        try:
            info = species_info(key)
            sci = info.get("canonicalName") or info.get("scientificName") or ""
            if is_excluded(sci) or info.get("rank") not in (None, "SPECIES"):
                continue
            rarity = "Zeer Zeldzaam" if count <= ZEER_ZELDZAAM_MAX else "Zeldzaam"
            meta = classify(info)
            meta.update({"name": dutch_name(key, sci), "scientificName": sci, "rarity": rarity})
            remember(key, meta)
            rare_meta[key] = meta
        except Exception as e:
            print(f"  Fout bij soort {key}: {e}")

    try:
        occ_per_key = fetch_occurrences_batch(tile, rare_meta.keys())
    except Exception as e:
        print(f"  Fout bij ophalen zeldzame soorten: {e}")
        occ_per_key = {}
    for key, meta in rare_meta.items():
        recs = build_records(occ_per_key.get(key, []), meta, inat_ids_in_gbif)
        if recs:
            add(recs)
            stats[meta["rarity"]] += 1

    # iNaturalist
    try:
        inat_obs = fetch_inat_observations(tile)
    except Exception as e:
        print(f"  Fout bij iNaturalist: {e}")
        inat_obs = []
    for obs in inat_obs:
        try:
            if str(obs.get("id")) in inat_ids_in_gbif:
                continue
            taxon = obs.get("taxon") or {}
            if taxon.get("rank") not in ("species", "subspecies"):
                continue
            sci = " ".join(str(taxon.get("name", "")).split()[:2])
            if not sci or is_excluded(sci):
                continue
            meta = meta_by_name.get(sci)
            if meta is None:
                key = match_key(sci)
                if key is None:
                    continue
                meta = meta_by_key.get(key)
                if meta is None:
                    if key in counts:
                        continue  # gewone soort in deze omgeving
                    info = species_info(key)
                    common = str(taxon.get("preferred_common_name") or "").strip()
                    meta = classify(info)
                    meta.update({
                        "name": (common[:1].upper() + common[1:]) if common else dutch_name(key, sci),
                        "scientificName": sci,
                        "rarity": "Zeer Zeldzaam",
                    })
                    remember(key, meta)
                    stats["Zeer Zeldzaam"] += 1
                meta_by_name[sci] = meta
            rec = build_inat_record(obs, meta)
            if rec:
                before = len(records)
                add([rec])
                stats["iNaturalist"] += len(records) - before
        except Exception as e:
            print(f"  Fout bij iNaturalist-waarneming {obs.get('id')}: {e}")

    return records

# ---------------------------------------------------------------------------
# Foto's per soort (iNaturalist, alleen Creative Commons)
# ---------------------------------------------------------------------------

OPEN_LICENTIES = {"cc0", "cc-by", "cc-by-sa", "cc-by-nc", "cc-by-nc-sa", "cc-by-nd", "cc-by-nc-nd"}
PHOTOS_FILE = os.path.join(OUTPUT_DIR, "photos.json")


def _usable_photo(photo):
    if not photo or str(photo.get("license_code") or "").lower() not in OPEN_LICENTIES:
        return None
    url = photo.get("medium_url") or str(photo.get("url") or "").replace("/square.", "/medium.")
    if not url.startswith("https://"):
        return None
    return {
        "url": url,
        "credit": str(photo.get("attribution") or "").strip(),
        "license": str(photo.get("license_code")).upper(),
    }


def find_species_photo(sci):
    d = inat_get("taxa", {"q": sci, "per_page": 5, "locale": "nl"})
    results = d.get("results", [])
    taxon = next((t for t in results if str(t.get("name", "")).lower() == sci.lower()), None)
    if not taxon:
        return None
    photo = _usable_photo(taxon.get("default_photo"))
    if photo:
        photo["taxon"] = taxon.get("id")
        return photo
    # Standaardfoto niet vrij te gebruiken: zoek een andere foto van deze soort
    full = inat_get(f"taxa/{taxon.get('id')}").get("results", [])
    for tp in (full[0].get("taxon_photos", []) if full else []):
        photo = _usable_photo(tp.get("photo"))
        if photo:
            photo["taxon"] = taxon.get("id")
            return photo
    return None


def update_photos(sci_names, deadline):
    try:
        with open(PHOTOS_FILE, encoding="utf-8") as f:
            photos = json.load(f)
    except Exception:
        photos = {}
    missing = sorted(n for n in sci_names if n and n not in photos)
    print(f"Foto's: {len(photos)} bekend, {len(missing)} nieuwe soorten opzoeken...")
    for sci in missing:
        if time.time() > deadline:
            print("  Tijdslimiet voor foto's bereikt; de rest volgt bij de volgende run.")
            break
        try:
            photos[sci] = find_species_photo(sci)  # None = geen vrije foto gevonden
        except Exception as e:
            print(f"  Geen foto voor {sci}: {e}")
    with open(PHOTOS_FILE, "w", encoding="utf-8") as f:
        json.dump(photos, f, ensure_ascii=False, indent=0)
    found = sum(1 for n in sci_names if photos.get(n))
    print(f"Foto's: {found} van {len(sci_names)} soorten hebben een foto.")

# ---------------------------------------------------------------------------
# Hoofdprogramma
# ---------------------------------------------------------------------------


def main():
    start = time.time()
    stats = {"Zeer Zeldzaam": 0, "Zeldzaam": 0, "iNaturalist": 0}
    tiles = make_tiles()

    # Diergroepen
    class_keys = []
    for cls_name, fallback in DIERGROEPEN.items():
        try:
            class_keys.append(match_key(cls_name, "CLASS") or fallback)
        except Exception:
            class_keys.append(fallback)

    # Vaste soorten (één keer opzoeken, geldt voor alle tegels)
    vaste_soorten = {}
    for sp in ALTIJD_TONEN:
        try:
            key = match_key(sp["scientificName"])
            if not key:
                print(f"Soort niet gevonden in GBIF: {sp['scientificName']}")
                continue
            meta = classify(species_info(key))
            meta.update({k: v for k, v in sp.items() if k in ("name", "emoji", "size", "sizeLabel", "rarity")})
            meta["scientificName"] = sp["scientificName"]
            vaste_soorten[key] = meta
        except Exception as e:
            print(f"Fout bij {sp['name']}: {e}")

    # Tegels verwerken (met tijdsbewaking)
    per_tile = {}
    deadline = start + MAX_LOOPTIJD_MIN * 60
    for i, tile in enumerate(tiles, 1):
        if time.time() > deadline:
            print(f"Tijdslimiet bereikt: tegel {tile['id']} en verder houden hun vorige gegevens.")
            break
        t0 = time.time()
        recs = process_tile(tile, class_keys, vaste_soorten, stats)
        per_tile[tile["id"]] = recs
        print(f"[{i}/{len(tiles)}] tegel {tile['id']}: {len(recs)} waarnemingen ({time.time() - t0:.0f} s)")

    # Handmatige meldingen in de juiste tegel
    for rec in handmatige_meldingen():
        t = tile_for_point(tiles, rec["lat"], rec["lng"])
        if t and t["id"] in per_tile:
            per_tile[t["id"]].append(rec)

    # Wegschrijven
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    index_tiles, total = [], 0
    all_sci = set()
    for tile in tiles:
        fname = f"tile_{tile['id']}.json"
        path = os.path.join(OUTPUT_DIR, fname)
        if tile["id"] in per_tile:          # deze run verwerkt: (over)schrijven of opruimen
            recs = per_tile[tile["id"]]
            if not recs:
                if os.path.exists(path):
                    os.remove(path)
                continue
            recs.sort(key=lambda x: x["seenAt"], reverse=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(recs, f, ensure_ascii=False, separators=(",", ":"))
        elif os.path.exists(path):          # niet aan de beurt gekomen: vorige versie houden
            with open(path, encoding="utf-8") as f:
                recs = json.load(f)
        else:
            continue
        all_sci.update(r.get("sci") for r in recs)
        index_tiles.append({**tile, "file": fname, "count": len(recs)})
        total += len(recs)

    update_photos({n for n in all_sci if n}, start + FOTO_LOOPTIJD_MIN * 60)

    with open(os.path.join(OUTPUT_DIR, "index.json"), "w", encoding="utf-8") as f:
        json.dump({
            "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "total": total,
            "tiles": index_tiles,
        }, f, ensure_ascii=False, indent=1)

    mins = (time.time() - start) / 60
    print(f"Klaar in {mins:.1f} min! {total} waarnemingen in {len(index_tiles)} tegels "
          f"({stats['Zeer Zeldzaam']} zeer zeldzame en {stats['Zeldzaam']} zeldzame soort-tegelcombinaties; "
          f"{stats['iNaturalist']} extra via iNaturalist).")


if __name__ == "__main__":
    main()