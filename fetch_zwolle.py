"""
AnimalRadar – data-ophaler (GBIF + iNaturalist)

Toont ALLE zeldzame en zeer zeldzame gewervelde dieren (vogels, zoogdieren,
reptielen, amfibieën) binnen de actieve regio.

Werkwijze:
  1. Tel per soort hoeveel waarnemingen er in de regio zijn (dit en vorig jaar).
  2. Soorten met weinig waarnemingen zijn in deze regio bijzonder:
       <= ZEER_ZELDZAAM_MAX  -> "Zeer Zeldzaam"
       <= ZELDZAAM_MAX       -> "Zeldzaam"
  3. Haal van die soorten de waarnemingen op, met Nederlandse naam.
  4. Soorten uit ALTIJD_TONEN komen er altijd bij (ook als ze vaker gezien worden).
  5. Aanvulling via iNaturalist (direct beschikbaar, zonder GBIF-vertraging);
     dubbelingen met GBIF worden overgeslagen.
  6. Kwetsbare soorten krijgen afgevlakte coördinaten (~1 km).
"""
import json
import time
import urllib.request
import urllib.parse
from datetime import datetime

# ---------------------------------------------------------------------------
# Instellingen
# ---------------------------------------------------------------------------

# Actieve regio: box rondom Zwolle (ca. 50 km straal)
LAT_MIN, LAT_MAX = 52.10, 52.95
LNG_MIN, LNG_MAX = 5.45, 6.75

# Periode: vorig jaar + dit jaar
THIS_YEAR = datetime.now().year
YEARS = f"{THIS_YEAR - 1},{THIS_YEAR}"

# Drempels (aantal waarnemingen in de regio in de periode)
ZEER_ZELDZAAM_MAX = 5
ZELDZAAM_MAX = 25

# Maximaal aantal waarnemingen per soort in de JSON
MAX_PER_SOORT = 25

# Diergroepen die meedoen (wetenschappelijke naam -> GBIF-key als terugval)
DIERGROEPEN = {"Aves": 212, "Mammalia": 359, "Reptilia": 358, "Amphibia": 131}

# Soorten die altijd getoond worden, met vaste naam/zeldzaamheid
ALTIJD_TONEN = [
    {"scientificName": "Canis lupus", "name": "Wolf", "emoji": "🐺", "size": "wolf", "sizeLabel": "Groot roofdier (Topfauna)", "rarity": "Zeer Zeldzaam", "badge": "bg-red-500/20 text-red-300 border-red-500/40"},
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

RARITY_BADGES = {
    "Zeer Zeldzaam": "bg-amber-500/20 text-amber-300 border-amber-500/40",
    "Zeldzaam": "bg-violet-500/20 text-violet-300 border-violet-500/40",
    "Vrij Zeldzaam": "bg-sky-500/20 text-sky-300 border-sky-500/40",
    "Regelmatig": "bg-emerald-500/20 text-emerald-300 border-emerald-500/40",
}

BASIS_FILTERS = {
    "decimalLatitude": f"{LAT_MIN},{LAT_MAX}",
    "decimalLongitude": f"{LNG_MIN},{LNG_MAX}",
    "hasCoordinate": "true",
    "hasGeospatialIssue": "false",
    "occurrenceStatus": "PRESENT",
    "year": YEARS,
    "basisOfRecord": ["HUMAN_OBSERVATION", "MACHINE_OBSERVATION", "OBSERVATION"],
}

# ---------------------------------------------------------------------------
# GBIF-hulpfuncties
# ---------------------------------------------------------------------------

API = "https://api.gbif.org/v1/"
INAT_API = "https://api.inaturalist.org/v1/"
_cache = {}

# iNaturalist-ID's die al via GBIF binnenkomen (om dubbelingen te voorkomen)
INAT_IDS_IN_GBIF = set()


def http_get(url, pause=0.05, retries=3):
    if url in _cache:
        return _cache[url]
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "AnimalRadar/2.1 (animalradar.nl)"})
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


def species_counts(class_keys):
    """Aantal waarnemingen per soort in de regio (via GBIF facets)."""
    counts, offset, page = {}, 0, 500
    while True:
        params = dict(BASIS_FILTERS)
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


def fetch_occurrences(taxon_key, limit):
    params = dict(BASIS_FILTERS)
    params.update({"taxonKey": taxon_key, "limit": limit})
    return gbif_get("occurrence/search", params).get("results", [])

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

# ---------------------------------------------------------------------------
# Records bouwen
# ---------------------------------------------------------------------------


def build_records(occurrences, meta):
    out = []
    protected = is_protected(meta)
    for occ in occurrences:
        if is_excluded(occ.get("scientificName")):
            continue
        if str(occ.get("institutionCode", "")).lower() == "inaturalist" and occ.get("catalogNumber"):
            INAT_IDS_IN_GBIF.add(str(occ.get("catalogNumber")))
        lat, lng = occ.get("decimalLatitude"), occ.get("decimalLongitude")
        event_date = (occ.get("eventDate") or "")[:10]
        if lat is None or lng is None or not event_date:
            continue

        if protected:
            # ~1 km nauwkeurig en geen exacte veldnaam
            lat, lng = round(float(lat), 2), round(float(lng), 2)
            place = occ.get("municipality") or occ.get("county") or occ.get("stateProvince") or "Regio Zwolle (50km)"
        else:
            lat, lng = round(float(lat), 4), round(float(lng), 4)
            place = occ.get("locality") or occ.get("municipality") or occ.get("stateProvince") or "Regio Zwolle (50km)"

        out.append({
            "id": occ.get("key"),
            "name": meta["name"],
            "scientificName": meta["scientificName"],
            "emoji": meta["emoji"],
            "size": meta["size"],
            "isLarge": meta["size"] in ("large", "wolf"),
            "sizeLabel": meta["sizeLabel"],
            "lat": lat,
            "lng": lng,
            "place": place,
            "seenAt": event_date,
            "rarity": meta["rarity"],
            "badge": meta["badge"],
            "protected": protected,
            "source": "GBIF",
            "url": f"https://www.gbif.org/occurrence/{occ.get('key')}" if occ.get("key") else "",
            "desc": occ.get("occurrenceRemarks") or occ.get("eventRemarks") or "",
        })
    return out


def is_protected(meta):
    return meta["rarity"] == "Zeer Zeldzaam" or meta["scientificName"] in KWETSBAAR

# ---------------------------------------------------------------------------
# iNaturalist (aanvulling: verschijnt direct, zonder GBIF-vertraging)
# ---------------------------------------------------------------------------

INAT_MAX_PAGES = 15  # 200 waarnemingen per pagina


def fetch_inat_observations():
    """Alle onderzoekswaardige (research grade) wilde waarnemingen in de regio."""
    results, page = [], 1
    while page <= INAT_MAX_PAGES:
        d = inat_get("observations", {
            "swlat": LAT_MIN, "swlng": LNG_MIN, "nelat": LAT_MAX, "nelng": LNG_MAX,
            "iconic_taxa": "Aves,Mammalia,Reptilia,Amphibia",
            "quality_grade": "research",
            "captive": "false",
            "d1": f"{THIS_YEAR - 1}-01-01",
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
    place_parts = [p.strip() for p in str(obs.get("place_guess") or "").split(",") if p.strip()]
    if protected:
        lat, lng = round(float(lat), 2), round(float(lng), 2)
        place = place_parts[-2] if len(place_parts) >= 2 else "Regio Zwolle (50km)"
    else:
        lat, lng = round(float(lat), 4), round(float(lng), 4)
        place = ", ".join(place_parts[:2]) if place_parts else "Regio Zwolle (50km)"

    return {
        "id": f"inat-{obs.get('id')}",
        "name": meta["name"],
        "scientificName": meta["scientificName"],
        "emoji": meta["emoji"],
        "size": meta["size"],
        "isLarge": meta["size"] in ("large", "wolf"),
        "sizeLabel": meta["sizeLabel"],
        "lat": lat,
        "lng": lng,
        "place": place,
        "seenAt": date,
        "rarity": meta["rarity"],
        "badge": meta["badge"],
        "protected": protected,
        "source": "iNaturalist",
        "url": f"https://www.inaturalist.org/observations/{obs.get('id')}",
        "desc": (obs.get("description") or "").strip()[:500],
    }


def handmatige_meldingen():
    """Handmatig ingevoerde, geverifieerde meldingen (bijv. uit BIJ12-rapportages of nieuws)."""
    return [{
        "id": "hasselt-wolf-2026-10",
        "name": "Wolf",
        "scientificName": "Canis lupus",
        "emoji": "🐺",
        "size": "wolf",
        "isLarge": True,
        "sizeLabel": "Groot roofdier (Topfauna)",
        "lat": 52.59,
        "lng": 6.10,
        "place": "Hasselt (Overijssel)",
        "seenAt": "2026-10-22",
        "rarity": "Zeer Zeldzaam",
        "badge": "bg-red-500/20 text-red-300 border-red-500/40",
        "protected": True,
        "source": "Handmatig (geverifieerd)",
        "url": "",
        "desc": "Geverifieerde melding van een wolf in de regio Hasselt / Zwarte Waterland.",
    }]

# ---------------------------------------------------------------------------
# Hoofdprogramma
# ---------------------------------------------------------------------------


def main():
    all_animals, seen = [], set()
    stats = {"altijd": 0, "Zeer Zeldzaam": 0, "Zeldzaam": 0, "iNaturalist": 0}
    meta_by_name, meta_by_key = {}, {}  # alle soorten die we tonen

    def remember(key, meta):
        meta_by_name[meta["scientificName"]] = meta
        meta_by_key[key] = meta

    def add(records):
        for item in records:
            sig = f"{item['name']}_{item['lat']}_{item['lng']}_{item['seenAt']}"
            if sig not in seen:
                seen.add(sig)
                all_animals.append(item)

    # 1. Diergroepen opzoeken
    class_keys = []
    for cls_name, fallback in DIERGROEPEN.items():
        try:
            class_keys.append(match_key(cls_name, "CLASS") or fallback)
        except Exception:
            class_keys.append(fallback)

    # 2. Vaste soorten
    vaste_keys = set()
    for sp in ALTIJD_TONEN:
        try:
            key = match_key(sp["scientificName"])
            if not key:
                print(f"Soort niet gevonden in GBIF: {sp['scientificName']}")
                continue
            vaste_keys.add(key)
            meta = classify(species_info(key))
            meta.update({k: v for k, v in sp.items() if k in ("name", "emoji", "size", "sizeLabel", "rarity", "badge")})
            meta["scientificName"] = sp["scientificName"]
            meta.setdefault("badge", RARITY_BADGES.get(meta["rarity"], RARITY_BADGES["Regelmatig"]))
            remember(key, meta)
            recs = build_records(fetch_occurrences(key, MAX_PER_SOORT), meta)
            add(recs)
            stats["altijd"] += 1
            print(f"[vast] {meta['name']}: {len(recs)} waarnemingen")
        except Exception as e:
            print(f"Fout bij {sp['name']}: {e}")

    # 3. Automatisch: alle soorten die in deze regio weinig gezien worden
    try:
        counts = species_counts(class_keys)
    except Exception as e:
        print(f"Fout bij tellen van soorten: {e}")
        counts = {}
    print(f"{len(counts)} soorten gevonden in de regio; zeldzame soorten worden opgehaald...")

    for key, count in sorted(counts.items(), key=lambda kv: kv[1]):
        if count > ZELDZAAM_MAX or key in vaste_keys:
            continue
        try:
            info = species_info(key)
            sci = info.get("canonicalName") or info.get("scientificName") or ""
            if is_excluded(sci) or info.get("rank") not in (None, "SPECIES"):
                continue
            rarity = "Zeer Zeldzaam" if count <= ZEER_ZELDZAAM_MAX else "Zeldzaam"
            meta = classify(info)
            meta.update({
                "name": dutch_name(key, sci),
                "scientificName": sci,
                "rarity": rarity,
                "badge": RARITY_BADGES[rarity],
            })
            remember(key, meta)
            recs = build_records(fetch_occurrences(key, min(count, MAX_PER_SOORT)), meta)
            if recs:
                add(recs)
                stats[rarity] += 1
        except Exception as e:
            print(f"Fout bij soort {key}: {e}")

    # 4. iNaturalist: extra (en snellere) waarnemingen van dezelfde zeldzame soorten,
    #    plus soorten die in GBIF nog helemaal niet in de regio voorkomen.
    try:
        inat_obs = fetch_inat_observations()
        print(f"iNaturalist: {len(inat_obs)} waarnemingen in de regio opgehaald, zeldzame worden gefilterd...")
    except Exception as e:
        print(f"Fout bij ophalen iNaturalist: {e}")
        inat_obs = []

    for obs in inat_obs:
        try:
            if str(obs.get("id")) in INAT_IDS_IN_GBIF:
                continue  # staat al via GBIF op de kaart
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
                    if key in counts or not counts:
                        continue  # gewone soort in deze regio (of telling mislukt)
                    # Nog geen enkele GBIF-waarneming in de regio: echt bijzonder
                    info = species_info(key)
                    common = str(taxon.get("preferred_common_name") or "").strip()
                    meta = classify(info)
                    meta.update({
                        "name": (common[:1].upper() + common[1:]) if common else dutch_name(key, sci),
                        "scientificName": sci,
                        "rarity": "Zeer Zeldzaam",
                        "badge": RARITY_BADGES["Zeer Zeldzaam"],
                    })
                    remember(key, meta)
                    stats["Zeer Zeldzaam"] += 1
                meta_by_name[sci] = meta
            rec = build_inat_record(obs, meta)
            if rec:
                before = len(all_animals)
                add([rec])
                stats["iNaturalist"] += len(all_animals) - before
        except Exception as e:
            print(f"Fout bij iNaturalist-waarneming {obs.get('id')}: {e}")

    # 5. Handmatige meldingen
    add(handmatige_meldingen())

    all_animals.sort(key=lambda x: x["seenAt"], reverse=True)
    with open("animals.json", "w", encoding="utf-8") as f:
        json.dump(all_animals, f, ensure_ascii=False, indent=2)

    print(f"Klaar! {len(all_animals)} waarnemingen opgeslagen in animals.json "
          f"({stats['altijd']} vaste soorten, {stats['Zeer Zeldzaam']} zeer zeldzame "
          f"en {stats['Zeldzaam']} zeldzame soorten; {stats['iNaturalist']} extra waarnemingen via iNaturalist).")


if __name__ == "__main__":
    main()