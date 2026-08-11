"""Repair bedtime/wake_time rows written before the timezone fix.

Rows were produced by datetime.fromtimestamp(ms), which applied the machine's
UTC offset on top of the offset Garmin had already baked into its *Local
epoch fields. Inverting is exact: .timestamp() undoes fromtimestamp(), and
reading the recovered epoch as UTC gives the true wall-clock time.

Ambiguous local times during a DST fall-back hour can round-trip to the wrong
side by one hour; at most a couple of rows per year are affected.
"""
import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import db  # noqa: E402


def corrected(iso_str):
    if not iso_str:
        return None
    naive = datetime.datetime.fromisoformat(iso_str)
    epoch = naive.timestamp()          # inverts fromtimestamp() in local tz
    return (datetime.datetime
            .fromtimestamp(epoch, tz=datetime.timezone.utc)
            .replace(tzinfo=None)
            .isoformat())


def main(apply_changes):
    conn = db.get_connection()
    db.init_db(conn)
    rows = conn.execute(
        "SELECT date, bedtime, wake_time FROM sleep "
        "WHERE bedtime IS NOT NULL OR wake_time IS NOT NULL"
    ).fetchall()

    shifts, updates = {}, []
    for r in rows:
        nb, nw = corrected(r["bedtime"]), corrected(r["wake_time"])
        if nb != r["bedtime"] or nw != r["wake_time"]:
            updates.append((nb, nw, r["date"]))
        if r["bedtime"] and nb:
            delta = (datetime.datetime.fromisoformat(r["bedtime"])
                     - datetime.datetime.fromisoformat(nb)).total_seconds() / 3600
            shifts[delta] = shifts.get(delta, 0) + 1

    print(f"righe totali: {len(rows)}   da correggere: {len(updates)}")
    print("distribuzione della correzione applicata:")
    for delta, count in sorted(shifts.items()):
        print(f"  -{delta:.0f} ora/e: {count} notti")

    print("\nesempi (prima -> dopo):")
    for nb, nw, date in updates[:5]:
        old = next(r["bedtime"] for r in rows if r["date"] == date)
        print(f"  {date}: {old} -> {nb}")

    if not apply_changes:
        print("\nDRY RUN — nessuna modifica scritta. Rilancia con --apply")
        return

    for nb, nw, date in updates:
        conn.execute("UPDATE sleep SET bedtime = ?, wake_time = ? WHERE date = ?",
                     (nb, nw, date))
    conn.commit()
    print(f"\napplicate {len(updates)} correzioni")


if __name__ == "__main__":
    main("--apply" in sys.argv)
