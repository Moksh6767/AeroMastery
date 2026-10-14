"""Builds mock CSVs + SQLite DB (mock_ops.db) with 5 tables. All data is fictional."""
import csv, random, sqlite3, os
from datetime import datetime, timedelta

random.seed(42)
OUT = os.path.dirname(os.path.abspath(__file__))
now = datetime(2026, 10, 14, 14, 0)

def iso(dt): return dt.strftime("%Y-%m-%d %H:%M")

# ---------- Aircraft_Status ----------
types = ["Fighter-A", "Fighter-B", "Transport-C", "Recon-D", "Helo-E"]
bases = ["Base Alpha", "Base Bravo", "Base Charlie"]
aircraft = []
for i in range(1, 21):
    t = random.choice(types)
    maint = random.choices(["OK", "Scheduled", "In Maintenance", "Grounded"], [10, 4, 3, 1])[0]
    aircraft.append({
        "aircraft_id": f"AC-{i:03d}",
        "type": t,
        "base": random.choice(bases),
        "fuel_pct": random.randint(15, 100),
        "maintenance_status": maint,
        "flight_hours_since_service": random.randint(5, 240),
        "next_service_due": iso(now + timedelta(days=random.randint(1, 45)))[:10],
        "mission_ready": int(maint == "OK"),
        "last_updated": iso(now - timedelta(minutes=random.randint(1, 180))),
    })

# ---------- Crew_Status ----------
first = ["Aarav", "Maya", "Liam", "Sofia", "Noah", "Zara", "Ethan", "Isha", "Omar", "Elena",
         "Kabir", "Nina", "Lucas", "Priya", "Diego", "Anika", "Sam", "Leila", "Ravi", "Tara"]
last = ["Sharma", "Reyes", "Chen", "Okafor", "Novak", "Silva", "Khan", "Moreau", "Das", "Ivanov"]
roles = ["Pilot", "Co-Pilot", "Navigator", "Flight Engineer", "Loadmaster"]
crew = []
for i in range(1, 31):
    fatigue = random.randint(1, 10)  # 1 = rested, 10 = exhausted
    hrs_rest = round(random.uniform(2, 14), 1)
    avail = "Available"
    if fatigue >= 8 or hrs_rest < 6: avail = "Rest Required"
    elif random.random() < 0.1: avail = "On Leave"
    elif random.random() < 0.1: avail = "Medical Hold"
    crew.append({
        "crew_id": f"CR-{i:03d}",
        "name": f"{random.choice(first)} {random.choice(last)}",
        "role": random.choice(roles),
        "fatigue_score": fatigue,
        "hours_since_rest": hrs_rest,
        "duty_hours_last_7d": random.randint(10, 60),
        "availability": avail,
        "assigned_aircraft_id": random.choice([a["aircraft_id"] for a in aircraft] + [None]),
        "last_updated": iso(now - timedelta(minutes=random.randint(1, 240))),
    })

# ---------- Inventory (weapons - generic mock categories only) ----------
items = [("Air-to-Air Missile (Generic)", "Missile"), ("Air-to-Ground Missile (Generic)", "Missile"),
         ("Guided Bomb (Generic)", "Bomb"), ("Unguided Bomb (Generic)", "Bomb"),
         ("20mm Rounds (belt)", "Ammunition"), ("Flare Pack", "Countermeasure"),
         ("Chaff Pack", "Countermeasure"), ("Drop Tank", "Fuel")]
inventory = []
n = 1
for b in bases:
    for name, cat in items:
        qty = random.randint(10, 400)
        minimum = random.randint(20, 120)
        inventory.append({
            "item_id": f"INV-{n:03d}", "base": b, "item_name": name, "category": cat,
            "quantity_on_hand": qty, "minimum_stock": minimum,
            "status": "Low" if qty < minimum else "OK",
            "last_audit": iso(now - timedelta(days=random.randint(0, 30)))[:10],
        })
        n += 1

# ---------- Weather ----------
conds = ["Clear", "Partly Cloudy", "Overcast", "Rain", "Thunderstorm", "Fog", "Dust"]
weather = []
for b in bases:
    for h in range(0, 24, 3):
        c = random.choice(conds)
        weather.append({
            "base": b, "forecast_time": iso(now + timedelta(hours=h)),
            "condition": c,
            "temp_c": random.randint(8, 38),
            "wind_kts": random.randint(0, 40),
            "wind_dir_deg": random.randrange(0, 360, 10),
            "visibility_km": round(random.uniform(0.5, 15), 1) if c in ("Fog", "Dust", "Rain") else round(random.uniform(8, 20), 1),
            "ceiling_ft": random.randint(500, 12000),
            "flight_safe": int(c not in ("Thunderstorm", "Fog")),
        })

# ---------- Active_Threats ----------
ttypes = ["Hostile Aircraft", "Surface-to-Air Site", "Naval Vessel", "Ground Convoy", "Drone Swarm"]
threats = []
for i in range(1, 13):
    threats.append({
        "threat_id": f"THR-{i:03d}",
        "threat_type": random.choice(ttypes),
        "latitude": round(random.uniform(20, 30), 4),
        "longitude": round(random.uniform(85, 95), 4),
        "severity": random.choice(["Low", "Medium", "High", "Critical"]),
        "confidence_pct": random.randint(40, 99),
        "status": random.choice(["Active", "Monitoring", "Neutralized"]),
        "first_detected": iso(now - timedelta(hours=random.randint(1, 48))),
        "last_seen": iso(now - timedelta(minutes=random.randint(1, 120))),
    })

tables = {
    "Aircraft_Status": aircraft, "Crew_Status": crew, "Inventory": inventory,
    "Weather": weather, "Active_Threats": threats,
}

con = sqlite3.connect(os.path.join(OUT, "mock_ops.db"))
for name, rows in tables.items():
    cols = list(rows[0].keys())
    with open(os.path.join(OUT, f"{name}.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)
    con.execute(f"DROP TABLE IF EXISTS {name}")
    con.execute(f"CREATE TABLE {name} ({', '.join(cols)})")
    con.executemany(f"INSERT INTO {name} VALUES ({','.join('?'*len(cols))})",
                    [tuple(r[c] for c in cols) for r in rows])
con.commit()
for name in tables:
    print(name, con.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0], "rows")
con.close()
