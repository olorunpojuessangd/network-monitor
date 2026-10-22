# network-monitor

A small Python service that watches my home network the way a help desk
technician watches a site's network: it checks key devices and services every
minute, records latency, packet loss, and DNS resolution time, flags
degradation, and shows trends on a local web dashboard.

## Purpose

One of the routine duties of a help desk technician is network monitoring.
Before users open tickets saying "the internet is slow", the technician should
already know whether the problem is:

- **the local network** (the router is slow or dropping packets),
- **the ISP / upstream** (the router is fine but public IPs like 1.1.1.1 are slow),
- **DNS** (pings are fine but name lookups are slow or failing), or
- **a single website** (everything else is healthy).

This project mirrors that duty. Each target in the config sits at a different
layer, and the monitor compares them automatically to say where a problem is.

## Features

- Pings a configurable list of targets (router, 1.1.1.1, 8.8.8.8, a few websites) every 60 seconds
- Records average latency, packet loss %, and DNS resolution time
- Stores every result in a SQLite database (`data/netmon.db`)
- Flags a target as **DEGRADED** when packet loss > 5%, latency > threshold, or DNS is slow/failing,
  and logs an alert to `logs/alerts.log` (only when the state changes, so the log isn't spammed)
- **Automatic diagnosis**: each cycle it works out which layer is at fault (local network,
  ISP, DNS, a single website) and explains it in plain English, the way a technician triages a ticket
- **Learned baselines**: flags latency that is unusual *for that target* (e.g. 2x its normal 24-hour
  median), not just over a fixed number
- **Desktop notifications** (macOS / Linux) when an incident opens, changes or resolves: one popup per incident
- **Self-health checks**: the dashboard warns if the monitor has stopped sending data, and the monitor warns
  about targets that never answer ping (probably misconfigured) and leaves them out of the diagnosis
- A local web dashboard with a diagnosis banner, warnings, status table, latency and packet-loss charts, and recent alerts
- No third-party Python packages: everything uses the Python standard library

## Setup

Requirements: **Python 3.9+** on macOS or Linux (it uses the system `ping` command).

```bash
# 1. Get the code
git clone https://github.com/olorunpojuessangd/network-monitor.git
cd network-monitor

# 2. Create your personal config (git-ignored, so it never gets committed)
cp config.example.json config.json

# 3. Find your router's IP and put it in config.json as the "Router" host
#    macOS:  route -n get default | grep gateway
#    Linux:  ip route | grep default

# 4. Start the monitor (leave this terminal running)
python3 monitor.py

# 5. In a second terminal, start the dashboard
python3 dashboard.py
#    then open http://127.0.0.1:8080
```

Run the unit tests with `python3 -m unittest`.

### Configuration

All settings live in `config.json` (falls back to `config.example.json`):

| Setting | Meaning |
|---|---|
| `interval_seconds` | How often to run a check cycle (default 60) |
| `ping_count` | Pings per target per cycle (default 10, so loss is measured in 10% steps) |
| `thresholds.packet_loss_pct` | Loss above this % is degraded (default 5) |
| `thresholds.latency_ms` | Average latency above this is degraded (default 100) |
| `thresholds.dns_ms` | DNS lookup slower than this is degraded (default 200) |
| `notifications` | `true` to show desktop popups for incidents |
| `baseline.window_hours` | History used to learn each target's normal latency (default 24) |
| `baseline.min_samples` | Results needed before judging against the baseline (default 30, about 30 minutes) |
| `baseline.multiplier` / `min_increase_ms` | Unusual = more than 2x normal **and** at least 10 ms above it |
| `health.stale_after_cycles` | Dashboard warns if no data for this many intervals (default 3) |
| `health.unresponsive_window_hours` | A target that never answers ping for this long is flagged as misconfigured (default 1) |
| `targets[]` | `name`, `host`, `role` (`gateway`, `internet` or `website`; guessed if left out); a target may override any threshold, e.g. `"latency_ms": 20` for the router |

### Running it in the background for a set period

To collect data for a week without keeping a terminal open:

```bash
./scripts/start_service.sh      # 7 days; pass a number for a different length, e.g. 3
```

This installs a macOS **LaunchAgent** (`~/Library/LaunchAgents/local.network-monitor.plist`), the
standard way macOS runs background programs. The script is commented line by line, but in short, launchd:

- starts `monitor.py` now and again whenever you log in,
- restarts it if it crashes (but not when it finishes normally),
- runs it under `caffeinate -i` so the Mac doesn't idle-sleep (closing a laptop lid still sleeps it),
- passes `--until <date>`, so after the period the monitor sends a "finished" notification and exits.

Useful commands while it runs:

```bash
tail -f logs/monitor.out.log                          # watch each check cycle live
cat logs/alerts.log                                   # just the alerts and incidents
launchctl print gui/$(id -u)/local.network-monitor | grep state   # is it running?
python3 dashboard.py                                  # then open http://127.0.0.1:8080
./scripts/stop_service.sh                             # stop early / clean up (data is kept)
```

Run `./scripts/stop_service.sh` after the period too, to remove the LaunchAgent file.

You can also run a timed session in the foreground: `python3 monitor.py --until 2026-10-29T09:00`.

## Architecture

```
            config.json
                 │
                 ▼
 ┌────────────────────────────────┐       ┌────────────────────────┐
 │ monitor.py  (every 60 s)       │       │ dashboard.py           │
 │                                │       │ http://127.0.0.1:8080  │
 │  checks.py    ping + DNS       │       │                        │
 │  baseline.py  what's normal?   │       │  health.py  monitor    │
 │  alerts.py    degraded? ───────┼──► logs/alerts.log   stopped?  │
 │  diagnosis.py which layer? ────┼──► notify.py (desktop popup)   │
 │  health.py    bad targets?     │       │                        │
 │  storage.py   save ────────────┼──► data/netmon.db ◄── reads ───┤
 └────────────────────────────────┘       │  /api/results          │
                                          │  /api/alerts           │
                                          │  /api/status           │
                                          └──────────┬─────────────┘
                                                     ▼
                                       static/dashboard.html (Chart.js)
```

**Diagnosis logic** (`netmon/diagnosis.py`), working from the inside out:

| What's degraded | Diagnosis | Suggested action |
|---|---|---|
| Router | **LAN** | Check Wi-Fi / restart router before calling the ISP |
| Router OK, all public IPs bad | **ISP** | Upstream / modem problem: contact the ISP with the data |
| Pings OK, most websites' DNS slow or failing | **DNS** | Switch DNS servers |
| Only some websites | **SITE** | Not your network |
| Some other mix | **PARTIAL** | Watch whether it spreads or clears |

| File | What it does |
|---|---|
| `monitor.py` | Main loop: runs all checks in parallel each cycle, evaluates, saves, alerts; optional `--until` end time |
| `analyze.py` | Turns the collected data into a findings report: coverage, baselines, time-of-day patterns traced to a layer, incidents |
| `dashboard.py` | Tiny web server: serves the HTML page and JSON API, reading from SQLite |
| `netmon/config.py` | Loads `config.json` (or the example) and resolves file paths |
| `netmon/checks.py` | Times DNS lookups and runs the system `ping`, parsing its summary |
| `netmon/storage.py` | SQLite schema and the functions that read/write results and alerts |
| `netmon/alerts.py` | Threshold + baseline rules, per-target DEGRADED/RECOVERED tracking, and network-wide INCIDENT/RESOLVED tracking |
| `netmon/baseline.py` | Learns each target's normal latency (24 h median) and spots unusual values |
| `netmon/diagnosis.py` | Decides which network layer is at fault each cycle |
| `netmon/health.py` | Self-checks: is the monitor still running? Does any target never answer ping? |
| `netmon/notify.py` | Desktop notifications via `osascript` (macOS) or `notify-send` (Linux) |
| `static/dashboard.html` | The dashboard page: status table, charts, alert list |
| `scripts/start_service.sh` / `stop_service.sh` | Install / remove the macOS background service for a timed run |
| `tests/test_checks.py` | Unit tests for ping parsing and alert logic |
| `tests/test_analyze.py` | Unit tests for incident pairing and time-of-day tracing in the report |
| `tests/test_intelligence.py` | Unit tests for diagnosis, baselines, health checks and config defaults |

**Design choices**

- *System `ping` instead of a Python ICMP library*: raw ICMP needs root, but `ping` already has that permission.
- *SQLite*: one file, no server, queryable with plain SQL (`sqlite3 data/netmon.db`).
- *Monitor and dashboard are separate processes*: the dashboard can be stopped/restarted without losing data.
- *Alert on state change*: one alert when something breaks and one when it recovers, like opening and closing a ticket.
- *Notify per incident, not per target*: when the router drops, every target goes bad. That's one LAN incident and one popup, not six.
- *Median, not average, for baselines*: a few huge spikes don't shift what counts as "normal".

**Known limitations**

- Some sites block ping (ICMP). If a website shows 100% loss but loads fine in a browser, that's why: swap it for another.
- DNS times can be near 0 ms after the first lookup because the OS caches answers. Spikes and failures still show up.
- Baselines need about 30 minutes of history before they start flagging; until then, only fixed thresholds apply.
- A long outage slowly raises the 24-hour baseline; fixed thresholds still catch big problems.
- The monitor can't notify you that it has *stopped*: that warning only appears on the dashboard.

## Security

- The dashboard listens on `127.0.0.1` only (not reachable from other devices) and is read-only.
- SQL uses parameterized queries; `ping` and `osascript` are run without a shell, with arguments passed separately, so hostnames and messages can't inject commands.
- Text from the database is HTML-escaped before display.
- No secrets are needed. Personal config, the database and logs are git-ignored.
- Not included: login or HTTPS for the dashboard. Don't expose it beyond localhost without adding them.

## Findings

_To be filled in after running the monitor for one to two weeks._

To generate the numbers, run:

```bash
python3 analyze.py --out reports/findings.md
```

The report covers how long the monitor actually ran, including any gaps; each target's normal latency;
latency by hour of day; and every incident. It also writes plain-English observations. When a time-of-day
slowdown appears, it compares your router at those hours with the rest of the day. If the router stayed
flat, the slowdown is upstream (your ISP); if the router slowed too, it starts inside your home. Treat the
observations as leads: confirm each one on the dashboard before writing it up.

**Monitoring period:** _start date – end date_

### Baseline (normal behaviour)
| Target | Typical latency | Typical loss | Typical DNS |
|---|---|---|---|
| Router | | | – |
| 1.1.1.1 | | | – |
| 8.8.8.8 | | | – |
| Websites | | | |

### Incidents observed
| When | What was degraded | Likely layer (LAN / ISP / DNS / site) | Duration | Notes |
|---|---|---|---|---|
| | | | | |

### Patterns
- _e.g. latency rises every evening between 7 and 10 pm_

### Controlled tests (validating the diagnosis)
Deliberately cause a problem and check the monitor diagnoses it correctly.

| Test | How | Expected diagnosis | Actual |
|---|---|---|---|
| Lose the router | Turn Wi-Fi off for 3 minutes | LAN | |
| Break DNS | Set DNS to an unused address (e.g. 10.255.255.1) in System Settings for 3 minutes | DNS | |
| Weak Wi-Fi | Work from the far end of the house for an hour | Router latency up vs baseline | |

### What I'd tell a user / what I'd escalate
- _e.g. evidence that would justify a call to the ISP_

### Lessons learned
-

Useful raw queries (the report covers most of this):

```sql
-- Average latency and loss per target
SELECT target, ROUND(AVG(latency_ms),1) AS avg_ms, ROUND(AVG(packet_loss_pct),2) AS avg_loss
FROM results GROUP BY target;

-- Every alert
SELECT ts, kind, message FROM alerts ORDER BY ts;

-- Every incident the diagnosis found, and how often each layer was at fault
SELECT ts, message FROM alerts WHERE kind IN ('INCIDENT', 'RESOLVED') ORDER BY ts;
SELECT layer, COUNT(*) AS minutes FROM diagnoses GROUP BY layer;
```
