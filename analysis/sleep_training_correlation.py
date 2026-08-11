"""Correlate training load/effect against sleep quality.

Uses Spearman as the primary statistic (rank-based: robust to outliers and
to non-linear-but-monotone relationships), Kendall for small subgroups with
many tied values, and an intensity-binned comparison to catch non-monotone
(inverted-U) dose-response, which no correlation coefficient can see.
"""
import math
from pathlib import Path
import sys
from collections import defaultdict

from scipy import stats as sp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import db  # noqa: E402

START, END = "2025-08-10", "2026-08-10"
MIN_N = 8  # below this, report but flag as unreliable


def spearman_ci(rho, n, alpha=0.05):
    """Fisher z-transform CI for Spearman's rho."""
    if n < 4 or abs(rho) >= 1:
        return None, None
    z = math.atanh(rho)
    se = 1.06 / math.sqrt(n - 3)
    crit = sp.norm.ppf(1 - alpha / 2)
    return math.tanh(z - crit * se), math.tanh(z + crit * se)


def report(name, x, y, use_kendall=False):
    n = len(x)
    if n < 4:
        print(f"  {name:<28} n={n:<4} — troppo pochi dati")
        return
    rho, p = sp.spearmanr(x, y)
    lo, hi = spearman_ci(rho, n)
    ci = f"[{lo:+.2f}, {hi:+.2f}]" if lo is not None else "[n/a]"
    flag = ""
    if p < 0.01:
        flag = " **"
    elif p < 0.05:
        flag = " *"
    warn = "  (n basso, inaffidabile)" if n < MIN_N else ""
    print(f"  {name:<28} n={n:<4} ρ={rho:+.2f}  p={p:.3f}  CI95={ci}{flag}{warn}")
    if use_kendall:
        tau, pk = sp.kendalltau(x, y)
        print(f"  {'  └ Kendall':<28} {'':<6} τ={tau:+.2f}  p={pk:.3f}")


def main():
    conn = db.get_connection()
    db.init_db(conn)

    sleep = {s["date"]: s for s in db.get_sleep(conn, START, END)
             if s["sleep_score"] is not None}
    acts = db.get_activities(conn, START, END)

    # One value per day: the hardest session of that day drives recovery load.
    day_effect, day_load, day_anaerobic = {}, {}, {}
    day_labels = defaultdict(list)
    for a in acts:
        d = a["date"]
        if a["training_effect"] is not None:
            day_effect[d] = max(day_effect.get(d, 0), a["training_effect"])
            if a["training_effect_label"]:
                day_labels[d].append((a["training_effect"], a["training_effect_label"]))
        if a["anaerobic_training_effect"] is not None:
            day_anaerobic[d] = max(day_anaerobic.get(d, 0), a["anaerobic_training_effect"])
        if a["load"] is not None:
            day_load[d] = day_load.get(d, 0) + a["load"]

    # Label of the day = label of that day's hardest session.
    day_label = {d: max(v)[1] for d, v in day_labels.items()}

    def paired(metric_map, lag=0):
        """Pairs of (training metric on day D, sleep score on day D+lag)."""
        import datetime
        out = []
        for d, val in metric_map.items():
            target = d
            if lag:
                target = (datetime.date.fromisoformat(d)
                          + datetime.timedelta(days=lag)).isoformat()
            if target in sleep:
                out.append((val, sleep[target]["sleep_score"]))
        return [p[0] for p in out], [p[1] for p in out]

    print("=" * 78)
    print("1. ALLENAMENTO → QUALITÀ SONNO (tutti gli allenamenti)")
    print("=" * 78)
    for label, m in (("Aerobic effect", day_effect),
                     ("Anaerobic effect", day_anaerobic),
                     ("Training load", day_load)):
        x, y = paired(m)
        report(f"{label} → sonno stessa notte", x, y)
    print()
    for label, m in (("Aerobic effect", day_effect),
                     ("Training load", day_load)):
        x, y = paired(m, lag=1)
        report(f"{label} → sonno notte dopo", x, y)

    print()
    print("=" * 78)
    print("2. PER TIPO DI STIMOLO (Kendall per i gruppi piccoli con molti ties)")
    print("=" * 78)
    by_label = defaultdict(lambda: ([], []))
    for d, lab in day_label.items():
        if d in sleep and d in day_effect:
            by_label[lab][0].append(day_effect[d])
            by_label[lab][1].append(sleep[d]["sleep_score"])
    for lab in sorted(by_label, key=lambda k: -len(by_label[k][0])):
        x, y = by_label[lab]
        report(lab, x, y, use_kendall=True)

    print()
    print("=" * 78)
    print("3. SONNO MEDIO PER TIPO DI ALLENAMENTO (vs giorni di riposo)")
    print("=" * 78)
    rest_nights = [s["sleep_score"] for d, s in sleep.items() if d not in day_effect]
    print(f"  {'RIPOSO (nessun allenamento)':<28} n={len(rest_nights):<4} "
          f"media={sum(rest_nights)/len(rest_nights):.1f}")
    groups = {}
    for lab in by_label:
        scores = by_label[lab][1]
        groups[lab] = scores
    for lab in sorted(groups, key=lambda k: -sum(groups[k]) / len(groups[k])):
        s = groups[lab]
        u, p = sp.mannwhitneyu(s, rest_nights, alternative="two-sided")
        flag = " **" if p < 0.01 else (" *" if p < 0.05 else "")
        warn = "  (n basso)" if len(s) < MIN_N else ""
        print(f"  {lab:<28} n={len(s):<4} media={sum(s)/len(s):.1f}  "
              f"p(vs riposo)={p:.3f}{flag}{warn}")

    print()
    print("=" * 78)
    print("4. CACCIA ALLA U ROVESCIATA (binning intensità — le correlazioni non la vedono)")
    print("=" * 78)
    bins = [(0.0, 2.0, "leggero  (TE 0-2)"),
            (2.0, 3.0, "moderato (TE 2-3)"),
            (3.0, 4.0, "intenso  (TE 3-4)"),
            (4.0, 5.1, "massimo  (TE 4-5)")]
    binned = []
    for lo, hi, name in bins:
        s = [sleep[d]["sleep_score"] for d, v in day_effect.items()
             if lo <= v < hi and d in sleep]
        if s:
            binned.append((name, s))
            print(f"  {name:<28} n={len(s):<4} media={sum(s)/len(s):.1f}  "
                  f"mediana={sorted(s)[len(s)//2]}")
    print(f"  {'riposo':<28} n={len(rest_nights):<4} "
          f"media={sum(rest_nights)/len(rest_nights):.1f}  "
          f"mediana={sorted(rest_nights)[len(rest_nights)//2]}")
    if len(binned) >= 3:
        h, p = sp.kruskal(*[s for _, s in binned])
        print(f"\n  Kruskal-Wallis fra le fasce: H={h:.2f}, p={p:.3f}"
              f"{' — differenza significativa' if p < 0.05 else ' — nessuna differenza'}")

    print()
    print("  * p<0.05   ** p<0.01")


if __name__ == "__main__":
    main()
