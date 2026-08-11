"""Does training intensity affect that night's sleep — low vs high intensity?

Hypothesis under test: zone-2 / low-intensity work helps sleep, high-intensity
work hurts it.

Design notes, applying the lessons from earlier mistakes in this analysis:
  - Direction: training on day D pairs with sleep[D+1], because Garmin labels a
    sleep record with the WAKE-UP date.
  - Friday and Saturday evenings excluded — that is where nights out live.
  - Collinearity between the predictor and sleep duration is checked BEFORE any
    partial correlation is trusted. Controlling for a near-collinear covariate
    produced a spurious (and sign-flipped) result earlier in this project.
  - Intensity is measured several ways, because average HR understates interval
    work: a HIIT session averages 66% of HRmax once its rest periods are counted.
"""
import datetime
from pathlib import Path
import sys
from collections import defaultdict

import numpy as np
from scipy import stats as sp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import db  # noqa: E402

START, END = "2025-08-10", "2026-08-11"
LOW_LABELS = {"RECOVERY", "AEROBIC_BASE"}
HIGH_LABELS = {"SPEED", "ANAEROBIC_CAPACITY", "VO2MAX", "LACTATE_THRESHOLD"}


def d(s):
    return datetime.date.fromisoformat(s)


def nxt(s):
    return (d(s) + datetime.timedelta(days=1)).isoformat()


def bh(iso):
    t = datetime.datetime.fromisoformat(iso)
    h = t.hour + t.minute / 60
    return h if h >= 12 else h + 24


def partial(x, y, z):
    x, y, z = map(np.asarray, (x, y, z))
    rx, ry, rz = (sp.rankdata(v) for v in (x, y, z))
    fx = rx - np.polyval(np.polyfit(rz, rx, 1), rz)
    fy = ry - np.polyval(np.polyfit(rz, ry, 1), rz)
    return sp.pearsonr(fx, fy)


def main():
    conn = db.get_connection()
    db.init_db(conn)
    sleep = {s["date"]: s for s in db.get_sleep(conn, START, END)
             if s["sleep_score"] is not None and s["bedtime"] and s["duration_min"]}
    daily = {x["date"]: x for x in db.get_daily_stats(conn, START, END)}
    hrv = {h["date"]: h for h in db.get_hrv(conn, START, END)}
    acts = db.get_activities(conn, START, END)

    hrmax = np.percentile([a["max_hr"] for a in acts if a["max_hr"]], 98)

    # nights out, so they can be excluded
    rows = []
    for k in sorted(set(sleep) & set(daily) & set(hrv)):
        v = (bh(sleep[k]["bedtime"]), sleep[k]["duration_min"],
             daily[k]["resting_hr"], hrv[k]["overall_hrv"])
        if None not in v:
            rows.append((k,) + v)
    base = [float(np.mean([r[i] for r in rows])) for i in range(1, 5)]
    nights_out = {k for k, b, du, hr, hv in rows
                  if sum([b >= base[0] + 1.5, du <= base[1] - 90,
                          hr >= base[2] + 10, hv <= base[3] * 0.85]) >= 2}

    # one row per training day: hardest session drives the day
    day = {}
    for a in acts:
        k = a["date"]
        cur = day.setdefault(k, {"load": 0.0, "aero": None, "anaero": None,
                                 "maxhr": None, "avghr": None, "label": None,
                                 "types": []})
        if a["load"] is not None:
            cur["load"] += a["load"]
        if a["training_effect"] is not None and (
                cur["aero"] is None or a["training_effect"] > cur["aero"]):
            cur["aero"] = a["training_effect"]
            cur["label"] = a["training_effect_label"]
        if a["anaerobic_training_effect"] is not None:
            cur["anaero"] = max(cur["anaero"] or 0, a["anaerobic_training_effect"])
        if a["max_hr"]:
            cur["maxhr"] = max(cur["maxhr"] or 0, a["max_hr"])
        if a["avg_hr"]:
            cur["avghr"] = max(cur["avghr"] or 0, a["avg_hr"])
        cur["types"].append(a["type"])

    # keep weekday evenings only, with a clean following night
    P = []
    for k, v in day.items():
        n = nxt(k)
        if d(k).weekday() in (4, 5):      # Friday/Saturday evening
            continue
        if n not in sleep or n in nights_out:
            continue
        if v["aero"] is None:
            continue
        P.append({**v, "date": k, "score": sleep[n]["sleep_score"],
                  "dur": sleep[n]["duration_min"], "bed": bh(sleep[n]["bedtime"])})

    rest = [sleep[k]["sleep_score"] for k in sleep
            if k not in nights_out and d(k).weekday() not in (5, 6)
            and (d(k) - datetime.timedelta(days=1)).isoformat() not in day]

    print("=" * 74)
    print("CAMPIONE: sere infrasettimanali con allenamento, serate fuori escluse")
    print("=" * 74)
    print(f"  notti analizzate: {len(P)}   (notti di riposo di confronto: {len(rest)})")
    print(f"  HRmax stimata: {hrmax:.0f} bpm\n")

    score = np.array([p["score"] for p in P], float)
    dur = np.array([p["dur"] for p in P], float)

    print("PRIMA DI TUTTO: l'intensità è collineare con la durata del sonno?")
    print("-" * 74)
    print("  (se lo fosse, la correlazione parziale sarebbe inaffidabile — è")
    print("   l'errore che ho commesso prima con l'ora di coricamento)\n")
    metrics = [
        ("training effect aerobico", np.array([p["aero"] for p in P], float)),
        ("training effect anaerobico", np.array([p["anaero"] or 0 for p in P], float)),
        ("carico totale", np.array([p["load"] for p in P], float)),
        ("HR max sessione (%HRmax)", np.array([(p["maxhr"] or np.nan) / hrmax * 100 for p in P])),
        ("HR media sessione (%HRmax)", np.array([(p["avghr"] or np.nan) / hrmax * 100 for p in P])),
    ]
    for nm, v in metrics:
        ok = ~np.isnan(v)
        r, _ = sp.spearmanr(v[ok], dur[ok])
        warn = "  <-- COLLINEARE, parziale non affidabile" if abs(r) > 0.7 else ""
        print(f"  {nm:<30} ρ con durata = {r:+.3f}{warn}")

    print()
    print("INTENSITÀ -> QUALITÀ DEL SONNO DELLA NOTTE STESSA")
    print("-" * 74)
    print(f"  {'metrica':<30} {'grezza':>16} {'a parità durata':>20}")
    for nm, v in metrics:
        ok = ~np.isnan(v)
        r, p = sp.spearmanr(v[ok], score[ok])
        rp, pp = partial(v[ok], score[ok], dur[ok])
        f = lambda rr, pp_: f"{rr:+.3f}{'**' if pp_ < 0.01 else '*' if pp_ < 0.05 else '  '}"
        print(f"  {nm:<30} {f(r,p):>16} {f(rp,pp):>20}   n={ok.sum()}")

    print()
    print("L'IPOTESI DIRETTA: bassa vs alta intensità")
    print("-" * 74)
    groups = {
        "bassa (recovery/aerobic base)": [p for p in P if p["label"] in LOW_LABELS],
        "alta (speed/anaerobic/vo2max)": [p for p in P if p["label"] in HIGH_LABELS],
        "tempo": [p for p in P if p["label"] == "TEMPO"],
    }
    for nm, g in groups.items():
        if len(g) >= 5:
            s = np.array([x["score"] for x in g], float)
            dd = np.array([x["dur"] for x in g], float)
            print(f"  {nm:<32} n={len(g):<4} score {s.mean():5.1f}   durata {dd.mean():.0f}min")
    print(f"  {'nessun allenamento (riposo)':<32} n={len(rest):<4} score {np.mean(rest):5.1f}")

    lo, hi = groups["bassa (recovery/aerobic base)"], groups["alta (speed/anaerobic/vo2max)"]
    if len(lo) >= 5 and len(hi) >= 5:
        sl = np.array([x["score"] for x in lo], float)
        sh = np.array([x["score"] for x in hi], float)
        u, p = sp.mannwhitneyu(sl, sh, alternative="two-sided")
        print(f"\n  bassa vs alta: Δ = {sl.mean()-sh.mean():+.1f} punti   p = {p:.3f}"
              f"{'  <-- significativo' if p < 0.05 else '  (nessuna differenza)'}")
        dl = np.array([x["dur"] for x in lo], float)
        dh = np.array([x["dur"] for x in hi], float)
        _, pd_ = sp.mannwhitneyu(dl, dh, alternative="two-sided")
        print(f"  controllo — le durate dei due gruppi differiscono? "
              f"{dl.mean():.0f} vs {dh.mean():.0f} min, p={pd_:.3f}")

    print()
    print("STESSO CONFRONTO MA A DURATA SIMILE (6.5-8h)")
    print("-" * 74)
    band = [p for p in P if 390 <= p["dur"] <= 480]
    bl = [p["score"] for p in band if p["label"] in LOW_LABELS]
    bhh = [p["score"] for p in band if p["label"] in HIGH_LABELS]
    print(f"  bassa intensità  n={len(bl):<3} score {np.mean(bl):.1f}" if bl else "  bassa: n/d")
    print(f"  alta intensità   n={len(bhh):<3} score {np.mean(bhh):.1f}" if bhh else "  alta: n/d")
    if len(bl) >= 5 and len(bhh) >= 5:
        u, p = sp.mannwhitneyu(bl, bhh, alternative="two-sided")
        print(f"  Δ = {np.mean(bl)-np.mean(bhh):+.1f}   p = {p:.3f}")

    print()
    print("PER ETICHETTA GARMIN (a durata simile)")
    print("-" * 74)
    by = defaultdict(list)
    for p in band:
        if p["label"]:
            by[p["label"]].append(p["score"])
    for lab, v in sorted(by.items(), key=lambda x: -np.mean(x[1])):
        flag = " (n basso)" if len(v) < 8 else ""
        print(f"  {lab:<22} n={len(v):<3} score {np.mean(v):5.1f}{flag}")
    big = [v for v in by.values() if len(v) >= 5]
    if len(big) >= 3:
        h, p = sp.kruskal(*big)
        print(f"\n  Kruskal-Wallis fra le etichette: H={h:.2f}  p={p:.3f}"
              f"{'  <-- differenza reale' if p < 0.05 else '  -> nessuna differenza'}")

    print()
    print("PER TIPO DI ATTIVITÀ (a durata simile)")
    print("-" * 74)
    byt = defaultdict(list)
    for p in band:
        for t in set(p["types"]):
            byt[t].append(p["score"])
    for t, v in sorted(byt.items(), key=lambda x: -len(x[1])):
        if len(v) >= 5:
            print(f"  {t:<22} n={len(v):<3} score {np.mean(v):5.1f}")

    print("\n  * p<0.05   ** p<0.01")


if __name__ == "__main__":
    main()
