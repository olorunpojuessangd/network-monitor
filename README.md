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
layer, so comparing them helps narrow down where a problem is.

## Features

- Pings a configurable list of targets (router, 1.1.1.1, 8.8.8.8, a few websites) every 60 seconds
- Records average latency, packet loss %, and DNS resolution time
- Stores every result in a SQLite database (`data/netmon.db`)
- Flags a target as **DEGRADED** when packet loss > 5%, latency > threshold, or DNS is slow/failing,
  and logs an alert to `logs/alerts.log` (only when the state changes, so the log isn't spammed)
- A local web dashboard with a status table, latency and packet-loss charts, and recent alerts
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
| `targets[]` | `name` + `host`; a target may override any threshold, e.g. `"latency_ms": 20` for the router |

### Keeping it running for weeks

The monitor only collects data while `monitor.py` is running and the computer
is awake. Simple options:

- Leave it in a terminal and use `caffeinate -i python3 monitor.py` (macOS) to stop the Mac sleeping.
- Run it on an always-on machine such as a Raspberry Pi.

## Architecture

```
            config.json
                 │
                 ▼
 ┌──────────────────────────────┐        ┌────────────────────────┐
 │ monitor.py  (every 60 s)     │        │ dashboard.py           │
 │                              │        │ http://127.0.0.1:8080  │
 │  checks.py   ping + DNS ─────┤        │                        │
 │  alerts.py   degraded? ──────┼──► logs/alerts.log              │
 │  storage.py  save ───────────┼──► data/netmon.db ◄── reads ────┤
 └──────────────────────────────┘        │  /api/results          │
                                         │  /api/alerts           │
                                         └──────────┬─────────────┘
                                                    ▼
                                      static/dashboard.html (Chart.js)
```

| File | What it does |
|---|---|
| `monitor.py` | Main loop: runs all checks in parallel each cycle, evaluates, saves, alerts |
| `dashboard.py` | Tiny web server: serves the HTML page and JSON API, reading from SQLite |
| `netmon/config.py` | Loads `config.json` (or the example) and resolves file paths |
| `netmon/checks.py` | Times DNS lookups and runs the system `ping`, parsing its summary |
| `netmon/storage.py` | SQLite schema and the functions that read/write results and alerts |
| `netmon/alerts.py` | Threshold rules and the DEGRADED/RECOVERED state tracker + alert log |
| `static/dashboard.html` | The dashboard page: status table, charts, alert list |
| `tests/test_checks.py` | Unit tests for ping parsing and alert logic |

**Design choices**

- *System `ping` instead of a Python ICMP library*: raw ICMP needs root, but `ping` already has that permission.
- *SQLite*: one file, no server, queryable with plain SQL (`sqlite3 data/netmon.db`).
- *Monitor and dashboard are separate processes*: the dashboard can be stopped/restarted without losing data.
- *Alert on state change*: one alert when something breaks and one when it recovers, like opening and closing a ticket.

**Known limitations**

- Some sites block ping (ICMP). If a website shows 100% loss but loads fine in a browser, that's why: swap it for another.
- DNS times can be near 0 ms after the first lookup because the OS caches answers. Spikes and failures still show up.
- Thresholds are fixed numbers, not learned baselines.

## Findings

_To be filled in after running the monitor for a few weeks._

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

### What I'd tell a user / what I'd escalate
- _e.g. evidence that would justify a call to the ISP_

### Lessons learned
-

Useful queries for this section:

```sql
-- Average latency and loss per target
SELECT target, ROUND(AVG(latency_ms),1) AS avg_ms, ROUND(AVG(packet_loss_pct),2) AS avg_loss
FROM results GROUP BY target;

-- Every alert
SELECT ts, kind, message FROM alerts ORDER BY ts;
```
