import json
import urllib.request
from datetime import datetime

# Centrum Zwolle met een zoekstraal van circa 50 km
LAT = 52.5168
LON = 6.0830
# 0.45 graad latitude ≈ 50 km, 0.70 graad longitude ≈ 48 km op deze breedtegraad
LAT_SPAN = 0.45
LON_SPAN = 0.70

ANIMAL_MAP = {
    "Canis lupus": {"name": "Wolf", "emoji": "🐺", "size": "wolf", "rarity": "Zeer Zeldzaam", "badge": "bg-red-500/20 text-red-300 border-red-500/40"},
    "Alcedo atthis": {"name": "IJsvogel", "emoji": "🐦", "size": "birds", "rarity": "Vrij Zeldzaam", "badge": "bg-sky-500/20 text-sky-300 border-sky-500/40"},
    "Buteo buteo": {"name": "Buizerd", "emoji": "🦅", "size": "birds", "rarity": "Algemeen", "badge": "bg-emerald-500/20 text-emerald-300 border-emerald-500/40"},
    "Haliaeetus albicilla": {"name": "Zeearend", "emoji": "🦅", "size": "birds", "rarity": "Zeldzaam", "badge": "bg-purple-500/20 text-purple-300 border-purple-500/40"},
    "Falco peregrinus": {"name": "Slechtvalk", "emoji": "🦅", "size": "birds", "rarity": "Vrij Zeldzaam", "badge": "bg-sky-500/20 text-sky-300 border-sky-500/40"},
    "Tyto alba": {"name": "Kerkuil", "emoji": "🦉", "size": "birds", "rarity": "Vrij Zeldzaam", "badge": "bg-sky-500/20 text-sky-300 border-sky-500/40"},
    "Bubo bubo": {"name": "Oehoe", "emoji": "🦉", "size": "birds", "rarity": "Zeer Zeldzaam", "badge": "bg-red-500/20 text-red-300 border-red-500/40"},
    "Capreolus capreolus": {"name": "Ree", "emoji": "🦌", "size": "large", "rarity": "Algemeen", "badge": "bg-emerald-500/20 text-emerald-300 border-emerald-500/40"},
    "Castor fiber": {"name": "Bever", "emoji": "🦫", "size": "medium", "rarity": "Vrij Zeldzaam", "badge": "bg-sky-500/20 text-sky-300 border-sky-500/40"},
    "Vulpes vulpes": {"name": "Vos", "emoji": "🦊", "size": "medium", "rarity": "Regelmatig", "badge": "bg-orange-500/20 text-orange-300 border-orange-500/40"},
    "Meles meles": {"name": "Das", "emoji": "🦡", "size": "medium", "rarity": "Vrij Zeldzaam", "badge": "bg-sky-500/20 text-sky-300 border-sky-500/40"},
    "Sus scrofa": {"name": "Wild Zwijn", "emoji": "🐗", "size": "large", "rarity": "Regelmatig", "badge": "bg-amber-500/20 text-amber-300 border-amber-500/40"},
    "Cervus elaphus": {"name": "Edelhert", "emoji": "🦌", "size": "large", "rarity": "Regelmatig", "badge": "bg-amber-500/20 text-amber-300 border-amber-500/40"},
}

def fetch_data():
    results = []
    min_lat = round(LAT - LAT_SPAN, 4)
    max_lat = round(LAT + LAT_SPAN, 4)
    min_lon = round(LON - LON_SPAN, 4)
    max_lon = round(LON + LON_SPAN, 4)

    # 100 meest recente waarnemingen in de 50km box rond Zwolle
    url = f"https://api.gbif.org/v1/occurrence/search?decimalLatitude={min_lat},{max_lat}&decimalLongitude={min_lon},{max_lon}&limit=100"
    
    req = urllib.request.Request(url, headers={'User-Agent': 'AnimalRadarZwolle/1.0'})
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())
            for item in data.get("results", []):
                species = item.get("species")
                if species in ANIMAL_MAP and "decimalLatitude" in item and "decimalLongitude" in item:
                    meta = ANIMAL_MAP[species]
                    event_date = item.get("eventDate", datetime.now().strftime("%Y-%m-%d"))
                    results.append({
                        "id": item.get("key"),
                        "name": meta["name"],
                        "emoji": meta["emoji"],
                        "size": meta["size"],
                        "isLarge": meta["size"] in ["large", "wolf"],
                        "lat": item["decimalLatitude"],
                        "lng": item["decimalLongitude"],
                        "place": item.get("locality") or "Regio Zwolle (50km)",
                        "seenAt": event_date[:10] if event_date else "Recent",
                        "rarity": meta["rarity"],
                        "badge": meta["badge"],
                        "desc": f"{meta['name']} geregistreerd binnen 50 km van Zwolle."
                    })
    except Exception as e:
        print(f"Fout bij ophalen: {e}")

    with open("animals.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"Succesvol {len(results)} waarnemingen opgeslagen binnen 50 km straal.")

if __name__ == "__main__":
    fetch_data()
