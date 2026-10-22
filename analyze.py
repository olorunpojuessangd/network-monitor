"""
analyze.py - Turns the collected data into a findings report (Markdown).

Run it any time, as often as you like (it only reads the database):

    python3 analyze.py                      # print the report
    python3 analyze.py --out reports/findings.md

The report answers the questions you'd be asked about the project:
  1. Coverage   - how long did it actually run, and where are the gaps?
                  (A closed laptop lid or a restart leaves gaps; be upfront about them.)
  2. Baselines  - what is "normal" for each target? (median and 95th percentile)
  3. Time of day - is the connection worse at certain hours (e.g. evenings)?
  4. Incidents  - every problem the diagnosis found: when, how long, which layer.
  5. Observations - plain-English sentences generated from the numbers above.
                  They are leads to check on the dashboard, not conclusions.

95th percentile ("p95") = the value that 95% of results are below. It shows
how bad the worst normal moments get, without being thrown off by one freak spike.
"""

import argparse
import statistics
from collections import defaultdict
from datetime import datetime

from netmon import storage
from netmon.config import guess_role, load_config, resolve_path

GAP_MINUTES = 5          # a pause longer than this between cycles counts as a gap
PEAK_FACTOR = 1.25       # an hour is a "peak" if its latency is 25%+ above the overall median


def to_local(ts):
    """'2026-10-22T04:10:11+00:00' (UTC) -> datetime in this computer's time zone."""
    return datetime.fromisoformat(ts).astimezone()


def fmt_time(dt):
    return dt.strftime("%a %b %d %H:%M")


def fmt_duration(minutes):
    if minutes < 60:
        return f"{minutes:.0f} min"
    if minutes < 48 * 60:
        return f"{minutes / 60:.1f} h"
    return f"{minutes / 1440:.1f} days"


def plural(n, word):
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def p95(values):
    if len(values) < 2:
        return values[0] if values else None
    return statistics.quantiles(values, n=20)[18]


def num(value, unit=" ms"):
    return "–" if value is None else f"{value:.1f}{unit}"


# ---------------------------------------------------------------- data loading

def load(conn, days):
    """All results (optionally only the last `days`), plus alerts and diagnoses."""
    hours = days * 24 if days else 24 * 365 * 10
    results = storage.recent_results(conn, hours)
    since = results[0]["ts"] if results else ""
    alerts = [dict(r) for r in conn.execute(
        "SELECT * FROM alerts WHERE ts >= ? ORDER BY ts", (since,))]
    diagnoses = [dict(r) for r in conn.execute(
        "SELECT layer, COUNT(*) AS n FROM diagnoses WHERE ts >= ? GROUP BY layer ORDER BY n DESC",
        (since,))]
    return results, alerts, diagnoses


# ---------------------------------------------------------------- analysis

def coverage(results, interval_seconds):
    """Monitoring period, % of expected cycles collected, and the longest gaps."""
    cycle_times = sorted({to_local(r["ts"]) for r in results})
    start, end = cycle_times[0], cycle_times[-1]
    expected = (end - start).total_seconds() / interval_seconds + 1
    gaps = []
    for before, after in zip(cycle_times, cycle_times[1:]):
        minutes = (after - before).total_seconds() / 60
        if minutes > GAP_MINUTES:
            gaps.append((before, after, minutes))
    gaps.sort(key=lambda g: g[2], reverse=True)
    return {"start": start, "end": end, "cycles": len(cycle_times),
            "pct": min(100.0, 100 * len(cycle_times) / expected), "gaps": gaps}


def per_target(results, roles):
    """Baseline numbers for each target."""
    grouped = defaultdict(list)
    for r in results:
        grouped[r["target"]].append(r)
    rows = []
    for name, rs in grouped.items():
        latencies = [r["latency_ms"] for r in rs if r["latency_ms"] is not None]
        dns = [r["dns_ms"] for r in rs if r["dns_ms"] is not None]
        rows.append({
            "target": name,
            "role": roles.get(name) or guess_role(rs[0]["host"]),
            "median": statistics.median(latencies) if latencies else None,
            "p95": p95(latencies),
            "loss": statistics.mean(r["packet_loss_pct"] for r in rs),
            "dns": statistics.median(dns) if dns else None,
            "degraded_pct": 100 * sum(r["degraded"] for r in rs) / len(rs),
        })
    return rows


def by_hour(results, roles):
    """Median latency and average loss for each hour of the day (local time),
    using the "internet" targets: they reflect your ISP connection as a whole.
    The router's median for each hour is kept alongside, so we can tell whether
    a slow hour starts inside your home (router slow too) or upstream (router flat)."""
    internet = {n for n, role in roles.items() if role == "internet"} or {r["target"] for r in results}
    gateway = {n for n, role in roles.items() if role == "gateway"}
    latency, loss, router = defaultdict(list), defaultdict(list), defaultdict(list)
    for r in results:
        hour = to_local(r["ts"]).hour
        if r["target"] in gateway and r["latency_ms"] is not None:
            router[hour].append(r["latency_ms"])
        if r["target"] not in internet:
            continue
        loss[hour].append(r["packet_loss_pct"])
        if r["latency_ms"] is not None:
            latency[hour].append(r["latency_ms"])
    all_latency = [v for vs in latency.values() for v in vs]
    overall = statistics.median(all_latency) if all_latency else None
    hours = [{"hour": h, "median": statistics.median(latency[h]) if latency[h] else None,
              "router": statistics.median(router[h]) if router[h] else None,
              "loss": statistics.mean(loss[h]), "samples": len(loss[h])}
             for h in sorted(loss)]
    return overall, hours


def incidents(alerts):
    """Pair each INCIDENT with the next RESOLVED to get start, end and duration.
    A change of layer mid-incident (e.g. DNS -> ISP) is kept as part of one incident."""
    found, current = [], None
    for a in alerts:
        if a["target"] != "Network":
            continue
        if a["kind"] == "INCIDENT":
            if current is None:
                current = {"start": to_local(a["ts"]), "messages": [a["message"]]}
            else:
                current["messages"].append(a["message"])
        elif a["kind"] == "RESOLVED" and current:
            current["end"] = to_local(a["ts"])
            found.append(current)
            current = None
    if current:
        current["end"] = None  # still ongoing at the end of the data
        found.append(current)
    for inc in found:
        end = inc["end"] or datetime.now().astimezone()
        inc["minutes"] = (end - inc["start"]).total_seconds() / 60
        inc["layer"] = inc["messages"][0].split("]")[0].strip("[")
    return found


def observations(cov, targets, overall, hours, incs, diagnoses):
    """Plain-English leads generated from the numbers."""
    notes = []
    days = (cov["end"] - cov["start"]).total_seconds() / 86400
    notes.append(f"Collected {cov['cycles']} check cycles over {days:.1f} days "
                 f"({cov['pct']:.0f}% coverage).")

    peaks = [h for h in hours if h["median"] and overall and h["median"] >= overall * PEAK_FACTOR
             and h["samples"] >= 30]
    if peaks:
        worst = max(peaks, key=lambda h: h["median"])
        span = ", ".join(f"{h['hour']:02d}:00" for h in peaks)
        note = (f"Internet latency is {100 * (worst['median'] / overall - 1):.0f}% above the "
                f"overall median ({overall:.1f} ms) at {span}; worst at "
                f"{worst['hour']:02d}:00 ({worst['median']:.1f} ms).")
        # Trace it: compare the router during peak hours with the router the rest of the day.
        peak_router = [h["router"] for h in peaks if h["router"] is not None]
        other_router = [h["router"] for h in hours if h not in peaks and h["router"] is not None]
        if peak_router and other_router:
            during, normal = statistics.median(peak_router), statistics.median(other_router)
            if during < normal * PEAK_FACTOR:
                note += (f" The router stayed flat at those hours ({during:.1f} ms vs {normal:.1f} ms "
                         "normally), so the slowdown is upstream of your home network - consistent "
                         "with evening ISP congestion.")
            else:
                note += (f" The router was slower too ({during:.1f} ms vs {normal:.1f} ms normally), "
                         "so the slowdown starts inside your home network (Wi-Fi or router load).")
        notes.append(note)
    elif len([h for h in hours if h["samples"] >= 30]) < 12:
        notes.append("Not enough data yet for a time-of-day pattern (needs at least 12 different hours "
                     "with 15+ cycles each - about a day of running).")
    elif overall:
        notes.append(f"No time-of-day pattern: internet latency stayed near {overall:.1f} ms around the clock.")

    gateway = [t for t in targets if t["role"] == "gateway"]
    if gateway and gateway[0]["p95"] and gateway[0]["median"] and gateway[0]["p95"] > 3 * gateway[0]["median"]:
        g = gateway[0]
        notes.append(f"Router latency is usually {g['median']:.1f} ms but its p95 is {g['p95']:.1f} ms: "
                     "occasional local (Wi-Fi) spikes worth correlating with time of day or device use.")

    if incs:
        by_layer = defaultdict(list)
        for inc in incs:
            by_layer[inc["layer"]].append(inc["minutes"])
        parts = [f"{layer}: {len(m)} ({fmt_duration(sum(m))} total)" for layer, m in by_layer.items()]
        longest = max(incs, key=lambda i: i["minutes"])
        notes.append(f"{plural(len(incs), 'incident')} detected - " + "; ".join(parts) + ". Longest: "
                     f"{fmt_duration(longest['minutes'])} on {fmt_time(longest['start'])} ({longest['layer']}).")
    else:
        notes.append("No incidents detected: every check cycle was within thresholds and baselines.")

    total = sum(d["n"] for d in diagnoses)
    healthy = next((d["n"] for d in diagnoses if d["layer"] == "OK"), 0)
    if total:
        notes.append(f"The network was diagnosed healthy {100 * healthy / total:.2f}% of the time.")
    return notes


# ---------------------------------------------------------------- report

def build_report(conn, config, days=None):
    results, alerts, diagnoses = load(conn, days)
    if not results:
        return "No data yet - run monitor.py first.\n"

    roles = {t["name"]: t["role"] for t in config["targets"]}
    cov = coverage(results, config["interval_seconds"])
    targets = per_target(results, roles)
    overall, hours = by_hour(results, roles)
    incs = incidents(alerts)

    out = [f"# Network findings report",
           f"_Generated {fmt_time(datetime.now().astimezone())} by analyze.py_", ""]

    out += ["## Observations (leads to verify on the dashboard)", ""]
    out += [f"- {n}" for n in observations(cov, targets, overall, hours, incs, diagnoses)] + [""]

    out += ["## Monitoring period", "",
            f"- From **{fmt_time(cov['start'])}** to **{fmt_time(cov['end'])}**",
            f"- {cov['cycles']} check cycles, **{cov['pct']:.0f}%** of the expected number"]
    if cov["gaps"]:
        out.append(f"- {plural(len(cov['gaps']), 'gap')} longer than {GAP_MINUTES} min (computer asleep or monitor "
                   "stopped). Longest:")
        out += [f"  - {fmt_time(a)} → {fmt_time(b)} ({fmt_duration(m)})" for a, b, m in cov["gaps"][:5]]
    out.append("")

    out += ["## Baseline per target", "",
            "| Target | Role | Median latency | p95 latency | Avg loss | Median DNS | Degraded |",
            "|---|---|---|---|---|---|---|"]
    out += [f"| {t['target']} | {t['role']} | {num(t['median'])} | {num(t['p95'])} | "
            f"{t['loss']:.2f}% | {num(t['dns'])} | {t['degraded_pct']:.1f}% of cycles |" for t in targets]
    out.append("")

    out += ["## Time of day (internet targets, local time)", "",
            f"Overall median: {num(overall)}. ▲ marks hours {100 * (PEAK_FACTOR - 1):.0f}%+ above it.", "",
            "| Hour | Median latency | Router | Avg loss | Samples |", "|---|---|---|---|---|"]
    for h in hours:
        flag = " ▲" if h["median"] and overall and h["median"] >= overall * PEAK_FACTOR else ""
        out.append(f"| {h['hour']:02d}:00 | {num(h['median'])}{flag} | {num(h['router'])} | "
                   f"{h['loss']:.2f}% | {h['samples']} |")
    out.append("")

    out += ["## Incidents", ""]
    if incs:
        out += ["| Start | Duration | Layer | Diagnosis |", "|---|---|---|---|"]
        for inc in incs:
            duration = fmt_duration(inc["minutes"]) + ("" if inc["end"] else " (ongoing)")
            summary = inc["messages"][0].split("] ", 1)[-1].replace("|", "/")
            out.append(f"| {fmt_time(inc['start'])} | {duration} | {inc['layer']} | {summary} |")
    else:
        out.append("None.")
    out.append("")

    if diagnoses:
        total = sum(d["n"] for d in diagnoses)
        out += ["## Time spent in each diagnosis", "", "| Layer | Cycles | Share |", "|---|---|---|"]
        out += [f"| {d['layer']} | {d['n']} | {100 * d['n'] / total:.2f}% |" for d in diagnoses]
        out.append("")
    return "\n".join(out)


def main():
    parser = argparse.ArgumentParser(description="Generate a findings report from the collected data")
    parser.add_argument("--days", type=float, help="only analyse the last N days (default: everything)")
    parser.add_argument("--out", help="write the report to this file instead of printing it")
    parser.add_argument("--db", help="database file to read (default: the one in config)")
    args = parser.parse_args()

    config = load_config()
    conn = storage.connect(args.db or resolve_path(config["database_path"]))
    report = build_report(conn, config, args.days)
    if args.out:
        resolve_path(args.out).write_text(report)
        print(f"Report written to {args.out}")
    else:
        print(report)


if __name__ == "__main__":
    main()
