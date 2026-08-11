"""Two follow-up analyses after single-day correlations came back flat.

1. Cumulative training load over rolling 3/7-day windows — fatigue accumulates
   over days, so a same-day snapshot may simply be measuring the wrong thing.
2. Extremes contrast — instead of fitting a line through all 349 nights, take
   the best and worst nights and ask what actually separates them, across
   every variable at once.
"""
import datetime
from pathlib import Path
import sys
from collections import defaultdict

from scipy import stats as sp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import db  # noqa: E402

START, END = "2025-08-10", "2026-08-10"
N_EXTREME = 30  # nights per tail


def d(s):
    return datetime.date.fromisoformat(s)


def bedtime_hour(iso_ts):
    """Hour on a 12->36 scale so post-midnight bedtimes sort after evening ones."""
    if not iso_ts:
        return None
    t = datetime.datetime.fromisoformat(iso_ts)
    h = t.hour + t.minute / 60
    return h if h >= 12 else h + 24


def load_data():
    conn = db.get_connection()
    db.init_db(conn)
    sleep = {s["date"]: s for s in db.get_sleep(conn, START, END)
             if s["sleep_score"] is not None}
    daily = {x["date"]: x for x in db.get_daily_stats(conn, START, END)}
    hrv = {h["date"]: h for h in db.get_hrv(conn, START, END)}
    acts = db.get_activities(conn, START, END)

    day_load, day_effect = defaultdict(float), {}
    day_labels = defaultdict(list)
    for a in acts:
        k = a["date"]
        if a["load"] is not None:
            day_load[k] += a["load"]
        if a["training_effect"] is not None:
            day_effect[k] = max(day_effect.get(k, 0), a["training_effect"])
            if a["training_effect_label"]:
                day_labels[k].append((a["training_effect"], a["training_effect_label"]))
    day_label = {k: max(v)[1] for k, v in day_labels.items()}
    return sleep, daily, hrv, dict(day_load), day_effect, day_label


def rolling_load(day_load, date_str, window):
    """Total training load over the `window` days ending on date_str."""
    base = d(date_str)
    return sum(day_load.get((base - datetime.timedelta(days=i)).isoformat(), 0.0)
               for i in range(window))


def part1(sleep, day_load):
    print("=" * 78)
    print("1. CARICO CUMULATIVO (finestra mobile) → QUALITÀ SONNO")
    print("=" * 78)
    print("  La fatica si accumula su giorni, non su ore. Il carico del singolo")
    print("  giorno potrebbe essere semplicemente la variabile sbagliata.\n")

    for window in (1, 3, 7, 14):
        xs, ys = [], []
        for date_str, s in sleep.items():
            # Require the whole window to be inside the synced range.
            if d(date_str) - datetime.timedelta(days=window - 1) < d(START):
                continue
            xs.append(rolling_load(day_load, date_str, window))
            ys.append(s["sleep_score"])
        rho, p = sp.spearmanr(xs, ys)
        flag = " **" if p < 0.01 else (" *" if p < 0.05 else "")
        print(f"  carico {window:>2}gg → sonno       n={len(xs):<4} "
              f"ρ={rho:+.2f}  p={p:.3f}{flag}")

    print()
    print("  Stesso test ma solo sui giorni con allenamento (esclude i riposi,")
    print("  che sono contaminati da malattia/viaggi/sbornie):")
    for window in (3, 7):
        xs, ys = [], []
        for date_str, s in sleep.items():
            if d(date_str) - datetime.timedelta(days=window - 1) < d(START):
                continue
            tot = rolling_load(day_load, date_str, window)
            if tot > 0:
                xs.append(tot)
                ys.append(s["sleep_score"])
        rho, p = sp.spearmanr(xs, ys)
        flag = " **" if p < 0.01 else (" *" if p < 0.05 else "")
        print(f"  carico {window:>2}gg → sonno       n={len(xs):<4} "
              f"ρ={rho:+.2f}  p={p:.3f}{flag}")


def part3(sleep, daily, hrv, day_load, day_effect, day_label):
    print()
    print("=" * 78)
    print(f"3. ESTREMI: {N_EXTREME} notti PEGGIORI vs {N_EXTREME} MIGLIORI")
    print("=" * 78)

    ranked = sorted(sleep.items(), key=lambda kv: kv[1]["sleep_score"])
    worst = dict(ranked[:N_EXTREME])
    best = dict(ranked[-N_EXTREME:])
    ws = [s["sleep_score"] for s in worst.values()]
    bs = [s["sleep_score"] for s in best.values()]
    print(f"  peggiori: score {min(ws)}-{max(ws)} (media {sum(ws)/len(ws):.1f})")
    print(f"  migliori: score {min(bs)}-{max(bs)} (media {sum(bs)/len(bs):.1f})")
    print()

    def getter(source, field):
        return lambda k: (source.get(k) or {}).get(field)

    variables = [
        ("HRV",                    getter(hrv, "overall_hrv")),
        ("stress medio",           getter(daily, "stress_avg")),
        ("stress max",             getter(daily, "stress_max")),
        ("HR riposo",              getter(daily, "resting_hr")),
        ("body battery min",       getter(daily, "body_battery_min")),
        ("body battery max",       getter(daily, "body_battery_max")),
        ("passi",                  getter(daily, "steps")),
        ("carico allenam. giorno", lambda k: day_load.get(k)),
        ("carico cumulato 3gg",    lambda k: rolling_load(day_load, k, 3) or None),
        ("carico cumulato 7gg",    lambda k: rolling_load(day_load, k, 7) or None),
        ("training effect",        lambda k: day_effect.get(k)),
        ("ora di coricamento",     lambda k: bedtime_hour((sleep.get(k) or {}).get("bedtime"))),
        ("durata sonno (min)",     getter(sleep, "duration_min")),
        ("sonno profondo (min)",   getter(sleep, "deep_min")),
        ("sonno REM (min)",        getter(sleep, "rem_min")),
        ("minuti svegli",          getter(sleep, "awake_min")),
    ]

    print(f"  {'variabile':<24} {'peggiori':>10} {'migliori':>10} {'p':>8}")
    print("  " + "-" * 56)
    results = []
    for name, fn in variables:
        wv = [v for v in (fn(k) for k in worst) if v is not None]
        bv = [v for v in (fn(k) for k in best) if v is not None]
        if len(wv) < 5 or len(bv) < 5:
            print(f"  {name:<24} {'dati insufficienti':>30}")
            continue
        _, p = sp.mannwhitneyu(wv, bv, alternative="two-sided")
        mw, mb = sum(wv) / len(wv), sum(bv) / len(bv)
        results.append((name, mw, mb, p))
        flag = " **" if p < 0.01 else (" *" if p < 0.05 else "")
        print(f"  {name:<24} {mw:>10.1f} {mb:>10.1f} {p:>8.4f}{flag}")

    # Weekend effect: nights out cluster on Fri/Sat, and those are behavioural,
    # not physiological — worth isolating.
    print()
    wk_w = sum(1 for k in worst if d(k).weekday() >= 5)
    wk_b = sum(1 for k in best if d(k).weekday() >= 5)
    _, p_wk = sp.fisher_exact([[wk_w, len(worst) - wk_w], [wk_b, len(best) - wk_b]])
    print(f"  notti nel weekend (sab/dom):  peggiori {wk_w}/{len(worst)}   "
          f"migliori {wk_b}/{len(best)}   p={p_wk:.3f}"
          f"{' *' if p_wk < 0.05 else ''}")

    train_w = sum(1 for k in worst if k in day_effect)
    train_b = sum(1 for k in best if k in day_effect)
    _, p_tr = sp.fisher_exact([[train_w, len(worst) - train_w],
                               [train_b, len(best) - train_b]])
    print(f"  giorni con allenamento:       peggiori {train_w}/{len(worst)}   "
          f"migliori {train_b}/{len(best)}   p={p_tr:.3f}"
          f"{' *' if p_tr < 0.05 else ''}")

    n_tests = len(results) + 2
    print()
    print(f"  Test eseguiti: {n_tests}. Soglia Bonferroni = {0.05/n_tests:.4f}")
    survivors = [r for r in results if r[3] < 0.05 / n_tests]
    if survivors:
        print("  Sopravvivono alla correzione per test multipli:")
        for name, mw, mb, p in sorted(survivors, key=lambda r: r[3]):
            direction = "più basso" if mw < mb else "più alto"
            print(f"    - {name}: {direction} nelle notti peggiori "
                  f"({mw:.1f} vs {mb:.1f}, p={p:.5f})")
    else:
        print("  Nessuna variabile sopravvive alla correzione.")

    print()
    print("  * p<0.05   ** p<0.01")


def main():
    sleep, daily, hrv, day_load, day_effect, day_label = load_data()
    part1(sleep, day_load)
    part3(sleep, daily, hrv, day_load, day_effect, day_label)


if __name__ == "__main__":
    main()
