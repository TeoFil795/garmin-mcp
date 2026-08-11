"""Definitive analysis, run after three data bugs were found and fixed.

Bugs that invalidated earlier runs:
  1. Sleep timestamps carried a double-applied UTC offset (+1h winter, +2h
     summer), so every bedtime was wrong and the seasonal jump added noise.
  2. body_battery_max stored the end-of-day reading instead of the peak.
  3. Garmin labels a sleep record with the WAKE-UP date, so sleep[D] is the
     night *preceding* day D's training. Earlier runs had the causal
     direction backwards.
"""
import datetime
import json
from pathlib import Path
import sys
from collections import defaultdict

import numpy as np
from scipy import stats as sp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import db  # noqa: E402

START, END = "2025-08-10", "2026-08-11"


def d(s):
    return datetime.date.fromisoformat(s)


def shift(s, days):
    return (d(s) + datetime.timedelta(days=days)).isoformat()


def bedtime_hour(iso):
    """Hour on a 12->36 scale so post-midnight bedtimes sort after evening ones."""
    if not iso:
        return None
    t = datetime.datetime.fromisoformat(iso)
    h = t.hour + t.minute / 60
    return h if h >= 12 else h + 24


def hhmm(v):
    v %= 24
    return f"{int(v):02d}:{int((v % 1) * 60):02d}"


def rho_line(name, x, y, extra=""):
    if len(x) < 5:
        print(f"  {name:<44} n={len(x):<4} — dati insufficienti")
        return None
    rho, p = sp.spearmanr(x, y)
    flag = " **" if p < 0.01 else (" *" if p < 0.05 else "")
    pstr = f"{p:.2e}" if p < 0.001 else f"{p:.3f}"
    print(f"  {name:<44} n={len(x):<4} ρ={rho:+.3f}  p={pstr}{flag}{extra}")
    return rho, p


def load():
    conn = db.get_connection()
    db.init_db(conn)
    sleep = {s["date"]: s for s in db.get_sleep(conn, START, END)
             if s["sleep_score"] is not None}
    daily = {x["date"]: x for x in db.get_daily_stats(conn, START, END)}
    hrv = {h["date"]: h for h in db.get_hrv(conn, START, END)}

    load_, effect, labels = defaultdict(float), {}, defaultdict(list)
    for a in db.get_activities(conn, START, END):
        k = a["date"]
        if a["load"] is not None:
            load_[k] += a["load"]
        if a["training_effect"] is not None:
            effect[k] = max(effect.get(k, 0), a["training_effect"])
            if a["training_effect_label"]:
                labels[k].append((a["training_effect"], a["training_effect_label"]))
    label = {k: max(v)[1] for k, v in labels.items()}
    return sleep, daily, hrv, dict(load_), effect, label


def section_direction(sleep, effect, load_):
    print("=" * 78)
    print("1. ALLENAMENTO E SONNO — CON LE DIREZIONI CORRETTE")
    print("=" * 78)
    print("  sleep[D] = notte che FINISCE la mattina di D, quindi PRECEDE")
    print("  gli allenamenti di D. Verificato su 349/349 record.\n")

    print("  A) Ti alleni -> come dormi QUELLA notte  (allenamento D vs sleep[D+1])")
    for nm, m in (("training effect", effect), ("carico totale", load_)):
        pairs = [(v, sleep[shift(k, 1)]["sleep_score"])
                 for k, v in m.items() if shift(k, 1) in sleep]
        rho_line(f"{nm} -> sonno della notte seguente",
                 [p[0] for p in pairs], [p[1] for p in pairs])

    print("\n  B) Come hai dormito -> quanto ti alleni DOPO  (sleep[D] vs allenamento D)")
    for nm, m in (("training effect", effect), ("carico totale", load_)):
        pairs = [(sleep[k]["sleep_score"], m[k]) for k in sleep if k in m]
        rho_line(f"sonno notte prima -> {nm}",
                 [p[0] for p in pairs], [p[1] for p in pairs])

    print("\n  C) Cosa dicevo prima (allineamento SBAGLIATO, per confronto)")
    pairs = [(v, sleep[k]["sleep_score"]) for k, v in effect.items() if k in sleep]
    rho_line("training effect vs sleep[D] — direzione invertita",
             [p[0] for p in pairs], [p[1] for p in pairs])


def section_bedtime(sleep):
    print()
    print("=" * 78)
    print("2. ORA DI CORICAMENTO -> QUALITÀ DEL SONNO")
    print("=" * 78)
    S = [s for s in sleep.values() if s["bedtime"] and s["duration_min"]]
    bed = np.array([bedtime_hour(s["bedtime"]) for s in S])
    dur = np.array([s["duration_min"] for s in S], float)
    score = np.array([s["sleep_score"] for s in S], float)

    print(f"  coricamento mediano: {hhmm(np.median(bed))}   "
          f"durata mediana: {np.median(dur)/60:.1f}h\n")
    rho_line("coricamento -> sleep score", bed, score)

    def partial(x, y, z):
        rx, ry, rz = (sp.rankdata(v) for v in (x, y, z))
        fx = rx - np.polyval(np.polyfit(rz, rx, 1), rz)
        fy = ry - np.polyval(np.polyfit(rz, ry, 1), rz)
        return sp.pearsonr(fx, fy)

    r, p = partial(bed, score, dur)
    print(f"  {'^ a parità di DURATA del sonno':<44} n={len(bed):<4} "
          f"ρ={r:+.3f}  p={p:.2e} **")

    print("\n  Fasce orarie:")
    bands = [(20, 23, "prima 23:00"), (23, 24, "23:00-00:00"),
             (24, 25, "00:00-01:00"), (25, 26, "01:00-02:00"),
             (26, 28, "02:00-04:00"), (28, 36, "dopo 04:00")]
    groups = []
    for lo, hi, name in bands:
        s = score[(bed >= lo) & (bed < hi)]
        if len(s):
            groups.append(s)
            print(f"    {name:<14} n={len(s):<4} media={s.mean():5.1f}")
    h, pk = sp.kruskal(*groups)
    print(f"    Kruskal-Wallis: H={h:.1f}  p={pk:.2e}")

    m = (dur >= 400) & (dur <= 500)
    early, late = score[m & (bed < 24.5)], score[m & (bed >= 24.5)]
    _, pm = sp.mannwhitneyu(early, late, alternative="two-sided")
    print(f"\n  Controprova a durata identica (6.7-8.3h, n={m.sum()}):")
    print(f"    prima 00:30 (n={len(early)}): {early.mean():.1f}")
    print(f"    dopo  00:30 (n={len(late)}): {late.mean():.1f}    p={pm:.2e}")
    sl, *_ = sp.linregress(bed, score)
    sl2, *_ = sp.linregress(bed, dur)
    print(f"\n  Per ogni ora di ritardo: {sl:.1f} punti di score, {sl2:.0f} min di sonno")


def section_nights_out(sleep, daily, hrv):
    print()
    print("=" * 78)
    print("3. SERATE FUORI — RICALCOLATE SUI DATI CORRETTI")
    print("=" * 78)
    dates = sorted(set(sleep) & set(daily) & set(hrv))
    rows = []
    for k in dates:
        b = bedtime_hour(sleep[k]["bedtime"])
        vals = (b, sleep[k]["duration_min"], daily[k]["resting_hr"],
                hrv[k]["overall_hrv"])
        if None not in vals:
            rows.append((k,) + vals)

    beds = [r[1] for r in rows]
    durs = [r[2] for r in rows]
    hrs = [r[3] for r in rows]
    hrvs = [r[4] for r in rows]
    base = (np.mean(beds), np.mean(durs), np.mean(hrs), np.mean(hrvs))
    print(f"  baseline su {len(rows)} notti: coricamento {hhmm(base[0])}, "
          f"durata {base[1]/60:.1f}h, HR {base[2]:.0f}, HRV {base[3]:.0f}\n")

    found = []
    for k, b, du, hr, hv in rows:
        sig = {
            "tardi": b >= base[0] + 1.5,
            "corto": du <= base[1] - 90,
            "HR alto": hr >= base[2] + 10,
            "HRV basso": hv <= base[3] * 0.85,
        }
        n = sum(sig.values())
        if n >= 2:
            found.append((k, n / 4, [x for x, v in sig.items() if v],
                          sleep[k]["sleep_score"], b))

    print(f"  RILEVATE: {len(found)} su {len(rows)} notti ({100*len(found)/len(rows):.0f}%)")
    print("  (la data è il RISVEGLIO: la serata è la sera prima)\n")
    print(f"  {'sera del':<12} {'conf':<6} {'letto':<7} {'score':<6} segnali")
    print("  " + "-" * 62)
    for k, conf, sigs, sc, b in sorted(found, key=lambda r: -r[1])[:20]:
        print(f"  {shift(k,-1):<12} {conf:<6.2f} {hhmm(b):<7} {sc:<6.0f} {', '.join(sigs)}")
    if len(found) > 20:
        print(f"  ... e altre {len(found)-20}")

    wd = defaultdict(int)
    names = ["lun", "mar", "mer", "gio", "ven", "sab", "dom"]
    for k, *_ in found:
        wd[d(shift(k, -1)).weekday()] += 1
    tot = defaultdict(int)
    for r in rows:
        tot[d(shift(r[0], -1)).weekday()] += 1
    print("\n  Per giorno della settimana (sera):")
    for i in range(7):
        pct = 100 * wd[i] / tot[i] if tot[i] else 0
        bar = "#" * int(pct / 3)
        print(f"    {names[i]}  {wd[i]:>2}/{tot[i]:<3} {pct:>5.1f}%  {bar}")
    return found


def main():
    sleep, daily, hrv, load_, effect, label = load()
    section_direction(sleep, effect, load_)
    section_bedtime(sleep)
    section_nights_out(sleep, daily, hrv)
    print("\n  * p<0.05   ** p<0.01")


if __name__ == "__main__":
    main()
