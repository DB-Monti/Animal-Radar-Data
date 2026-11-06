import json
import urllib.request
import urllib.parse
from datetime import datetime

# Coördinaten box rondom Zwolle (ca. 50 km straal)
LAT_MIN, LAT_MAX = 52.10, 52.95
LNG_MIN, LNG_MAX = 5.45, 6.75

# Configuratie van de Zeldzame Topvogels & Topfauna
# Hierin zijn Kraanvogels toegevoegd als 'birds' met rarity 'Vrij Zeldzaam', 
# en de rest van de Top 25 zeldzame waarnemingen.
SPECIES_CONFIG = [
    {"key": 2475532, "name": "Kraanvogel", "emoji": "🦩", "size": "birds", "isLarge": True, "rarity": "Vrij Zeldzaam", "badge": "bg-sky-500/20 text-sky-300 border-sky-500/40", "limit": 25},
    {"key": 2480446, "name": "Zeearend", "emoji": "🦅", "size": "birds", "isLarge": True, "rarity": "Zeer Zeldzaam", "badge": "bg-amber-500/20 text-amber-300 border-amber-500/40", "limit": 25},
    {"key": 2480506, "name": "Visarend", "emoji": "🦅", "size": "birds", "isLarge": True, "rarity": "Zeer Zeldzaam", "badge": "bg-amber-500/20 text-amber-300 border-amber-500/40", "limit": 25},
    {"key": 2498237, "name": "Oehoe", "emoji": "🦉", "size": "birds", "isLarge": True, "rarity": "Zeer Zeldzaam", "badge": "bg-amber-500/20 text-amber-300 border-amber-500/40", "limit": 25},
    {"key": 2481069, "name": "Slechtvalk", "emoji": "🦅", "size": "birds", "isLarge": False, "rarity": "Vrij Zeldzaam", "badge": "bg-sky-500/20 text-sky-300 border-sky-500/40", "limit": 25},
    {"key": 2481912, "name": "Zwarte Ooievaar", "emoji": "🦩", "size": "birds", "isLarge": True, "rarity": "Zeer Zeldzaam", "badge": "bg-amber-500/20 text-amber-300 border-amber-500/40", "limit": 25},
    {"key": 2482468, "name": "Raaf", "emoji": "🐦‍⬛", "size": "birds", "isLarge": False, "rarity": "Zeldzaam", "badge": "bg-amber-500/20 text-amber-300 border-amber-500/40", "limit": 25},
    {"key": 5219173, "name": "Wolf", "emoji": "🐺", "size": "wolf", "isLarge": True, "rarity": "Zeer Zeldzaam", "badge": "bg-red-500/20 text-red-300 border-red-500/40", "limit": 25},
    {"key": 2440958, "name": "Edelhert", "emoji": "🦌", "size": "large", "isLarge": True, "rarity": "Vrij Zeldzaam", "badge": "bg-amber-500/20 text-amber-300 border-amber-500/40", "limit": 15},
    {"key": 2441176, "name": "Wild zwijn", "emoji": "🐗", "size": "large", "isLarge": True, "rarity": "Regelmatig", "badge": "bg-emerald-500/20 text-emerald-300 border-emerald-500/40", "limit": 15}
]

def fetch_species(sp):
    params = {
        "taxonKey": sp["key"],
        "decimalLatitude": f"{LAT_MIN},{LAT_MAX}",
        "decimalLongitude": f"{LNG_MIN},{LNG_MAX}",
        "hasCoordinate": "true",
        "year": "2025,2026",
        "limit": sp["limit"]
    }
    url = "https://api.gbif.org/v1/occurrence/search?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "AnimalRadarZwolle/1.1"})
    
    results = []
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            for occ in data.get("results", []):
                lat = occ.get("decimalLatitude")
                lng = occ.get("decimalLongitude")
                if lat is None or lng is None:
                    continue
                
                event_date = occ.get("eventDate", "")[:10]
                if not event_date:
                    continue

                locality = occ.get("locality") or occ.get("municipality") or occ.get("stateProvince") or "Regio Zwolle (50km)"
                remarks = occ.get("occurrenceRemarks") or occ.get("eventRemarks") or occ.get("dynamicProperties") or ""
                if remarks and remarks.startswith("{"):
                    remarks = "" 

                results.append({
                    "id": occ.get("key"),
                    "name": sp["name"],
                    "emoji": sp["emoji"],
                    "size": sp["size"],
                    "isLarge": sp["isLarge"],
                    "lat": round(float(lat), 4),
                    "lng": round(float(lng), 4),
                    "place": locality,
                    "seenAt": event_date,
                    "rarity": sp["rarity"],
                    "badge": sp["badge"],
                    "desc": remarks
                })
    except Exception as e:
        print(f"Fout bij ophalen {sp['name']}: {e}")
    return results

def main():
    all_animals = []
    seen_coords = set()

    for sp in SPECIES_CONFIG:
        items = fetch_species(sp)
        for item in items:
            sig = f"{item['name']}_{item['lat']}_{item['lng']}_{item['seenAt']}"
            if sig not in seen_coords:
                seen_coords.add(sig)
                all_animals.append(item)

    # Sorteer op datum (nieuwste waarnemingen eerst)
    all_animals.sort(key=lambda x: x["seenAt"], reverse=True)

    with open("animals.json", "w", encoding="utf-8") as f:
        json.dump(all_animals, f, ensure_ascii=False, indent=2)
    
    print(f"Klaar! {len(all_animals)} topfauna waarnemingen opgeslagen in animals.json.")

if __name__ == "__main__":
    main()
