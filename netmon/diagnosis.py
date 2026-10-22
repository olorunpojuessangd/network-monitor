"""
diagnosis.py - Automatic triage: WHERE is the problem?

This is the reasoning a help desk technician does when a user says "the
internet is slow". Each target has a role, and the roles form layers:

    this computer -> gateway (router) -> internet (1.1.1.1, 8.8.8.8) -> websites
                                                         DNS ^ (name lookups)

Working from the inside out:
  1. Router degraded           -> LAN problem (Wi-Fi / router). Everything past
                                  it will look bad too, so blame the first bad hop.
  2. Router fine, ALL public
     IPs degraded              -> ISP / upstream problem.
  3. Pings fine, but name
     lookups failing or slow
     for most websites         -> DNS problem.
  4. Only some websites bad    -> those sites' problem, not yours.
  5. Anything else degraded    -> PARTIAL: no single layer stands out.

Layers returned: OK, LAN, ISP, DNS, SITE, PARTIAL.
"""


def _names(entries):
    return ", ".join(e["name"] for e in entries)


def _has_dns_problem(entry):
    return entry["dns_failed"] or "DNS" in entry["reason"]


def diagnose(entries, ignore=()):
    """
    entries: one dict per target for this cycle, with keys
             name, role, degraded, reason, dns_failed
    ignore:  target names to leave out (e.g. ones health.py flagged as misconfigured)
    Returns (layer, summary_sentence).
    """
    entries = [e for e in entries if e["name"] not in ignore]
    by_role = {role: [e for e in entries if e["role"] == role]
               for role in ("gateway", "internet", "website")}
    bad = [e for e in entries if e["degraded"]]

    if not bad:
        return "OK", "All targets healthy."

    gateways = by_role["gateway"]
    if gateways and all(e["degraded"] for e in gateways):
        return "LAN", (f"Router ({_names(gateways)}) is degraded: {gateways[0]['reason']}. "
                       "Likely Wi-Fi or the router itself - check the local connection "
                       "or restart the router before calling the ISP.")

    internet = by_role["internet"]
    if internet and all(e["degraded"] for e in internet):
        return "ISP", ("Router is fine but every public server is degraded "
                       f"({_names(internet)}). Likely an ISP or modem problem upstream.")

    websites = by_role["website"]
    dns_bad = [e for e in websites if _has_dns_problem(e)]
    if websites and len(dns_bad) >= max(1, (len(websites) + 1) // 2):
        return "DNS", (f"Name lookups are failing or slow for {_names(dns_bad)} while pings "
                       "to public IPs work. Likely a DNS problem - try switching DNS "
                       "servers (e.g. 1.1.1.1 or 8.8.8.8).")

    if all(e["role"] == "website" for e in bad):
        return "SITE", (f"Only {_names(bad)} degraded; the rest of the network is healthy, "
                        "so the problem is likely at that site, not on your side.")

    return "PARTIAL", (f"Some targets degraded ({_names(bad)}) but no single layer stands "
                       "out. Watch whether it spreads or clears.")
