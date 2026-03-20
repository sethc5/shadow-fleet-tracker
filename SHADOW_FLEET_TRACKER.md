# Shadow Fleet Tracker

Open-source tool for monitoring sanctioned Russian oil tankers — cross-referencing AIS vessel movement data against sanctions lists to flag evasion behavior and publish daily digests for journalists, NGOs, and enforcement agencies.

**Why this exists:** Russia earns ~€510M/day from oil exports. The shadow fleet — ~600 tankers operating outside Western sanctions compliance — is the primary mechanism. Enforcement is episodic because monitoring is manual. This tool makes it systematic and public.

---

## The Problem

Shadow fleet vessels evade sanctions using a predictable playbook:
- AIS transponder disabled or spoofed (going "dark")
- Flag changes to permissive registries (Cameroon, Sierra Leone, Comoros, Palau)
- Ship-to-ship (STS) transfers in international waters to obscure cargo origin
- Ownership transfers to anonymous shell companies
- Routing through non-sanctioning jurisdictions (India, China)

Detection is currently done manually by journalists (RFE/RL, Reuters), commercial intelligence firms (Windward, Kpler — paywalled), and NGOs (CREA). No free, open, continuously-updated public tool exists.

**The enforcement gap:** OFAC-sanctioned vessels drop Russian oil carrying capacity ~73% after designation. The EU has sanctioned ~⅔ of the fleet; OFAC only ~40%. Every unsanctioned vessel that gets flagged publicly accelerates the designation pipeline.

---

## What This Tool Does

1. **Ingests** vessel movement data from public AIS feeds
2. **Cross-references** against multiple sanctions lists (OFAC SDN, EU, UK OFSI, OpenSanctions)
3. **Scores** vessels on evasion behavior indicators (AIS gaps, flag changes, STS activity, Russian port calls)
4. **Flags** vessels approaching sanctioned thresholds but not yet designated
5. **Publishes** a daily digest: new flags, active vessels near EU/NATO waters, high-risk movements
6. **Exposes** a simple API for journalists and NGOs to query vessel status

---

## Data Sources

### AIS / Vessel Movement (free/freemium)
| Source | Notes | Link |
|---|---|---|
| **MarineTraffic** | Free tier available; public API for vessel positions | [marinetraffic.com](https://www.marinetraffic.com/en/ais/details/ships) |
| **VesselFinder** | Free AIS data; vessel history | [vesselfinder.com](https://www.vesselfinder.com) |
| **AISHub** | Free AIS data sharing network | [aishub.net](https://www.aishub.net) |
| **BarentsWatch** | Norwegian coast guard AIS — good Baltic/Arctic coverage | [barentswatch.no](https://www.barentswatch.no/en/open-api/) |

### Sanctions Lists (free, machine-readable)
| Source | Notes | Link |
|---|---|---|
| **OFAC SDN List** | CSV/XML download, daily updates. Vessels tagged with IMO numbers | [ofac.treasury.gov/sanctions-list-service](https://ofac.treasury.gov/sanctions-list-service) |
| **OpenSanctions** | Aggregates OFAC, EU, UK, UN + shadow fleet tag. Free for non-commercial. 706 shadow fleet vessels indexed | [opensanctions.org](https://www.opensanctions.org/search/?scope=default&schema=Vessel) |
| **EU Sanctions Map** | EU Council designations, updated continuously | [sanctionsmap.eu](https://www.sanctionsmap.eu) |
| **TankerTrackers** | 1,303 sanctioned tankers listed; public API + CSV download | [tankertrackers.com/report/sanctioned](https://tankertrackers.com/report/sanctioned) |
| **CREA** | Centre for Research on Energy and Clean Air — Russia oil revenue analysis | [energyandcleanair.org](https://energyandcleanair.org) |

### Reference / Watchlists
| Source | Notes | Link |
|---|---|---|
| **IMO GISIS** | Official IMO vessel registry; false flag records | [gisis.imo.org](https://gisis.imo.org) |
| **Lloyd's List** | Paywalled but some public vessel data | [lloydslist.com](https://lloydslist.com) |
| **Kyiv School of Economics** | Russia war revenue tracker | [kse.ua](https://kse.ua/war-and-sanctions) |

---

## Evasion Behavior Scoring

Each vessel gets a risk score (0–100) based on weighted indicators:

| Indicator | Weight | Signal |
|---|---|---|
| AIS dark event (>6 hours) near Russian port | High | Active evasion |
| AIS dark event in open water | Medium | Possible STS transfer |
| Flag change in past 90 days | High | Registry shopping |
| Flag is high-risk registry (Cameroon, Sierra Leone, Comoros, Palau, Cook Islands) | Medium | Permissive jurisdiction |
| Russian port call in past 30 days | High | Direct Russia nexus |
| STS activity in past 60 days | High | Cargo obfuscation |
| Ownership change in past 90 days | Medium | Shell company rotation |
| Vessel age > 20 years | Low | Shadow fleet profile |
| Named on any sanctions list | Definitive | Already designated |

Vessels scoring >60 but not yet designated = **primary alert targets** — these are the ones worth publishing and referring to enforcement agencies.

---

## Tech Stack

Intentionally simple. One person, LLM-assisted development.

```
Python 3.11+
├── Data ingestion:     requests, aiohttp (async AIS polling)
├── Sanctions parsing:  pandas, xml.etree (OFAC XML), opensanctions API
├── Storage:            SQLite (dev) → PostgreSQL (prod)
├── Scoring engine:     pure Python, rule-based + configurable weights
├── Output:             Markdown digest (daily), JSON API (FastAPI), optional Telegram bot
└── Deployment:         Docker, runs on a $5/mo VPS
```

No ML required for v1. Rule-based scoring is transparent, auditable, and explainable to journalists — which matters for credibility.

---

## Architecture

```
┌─────────────────────────────────────────┐
│           Data Ingestion Layer          │
│  AIS feeds → vessel positions (hourly)  │
│  Sanctions lists → designations (daily) │
└────────────────────┬────────────────────┘
                     │
┌────────────────────▼────────────────────┐
│           Vessel State Store            │
│  SQLite/Postgres: positions, history,   │
│  flag history, ownership, sanctions     │
└────────────────────┬────────────────────┘
                     │
┌────────────────────▼────────────────────┐
│           Scoring Engine                │
│  Rule-based evasion indicator scoring   │
│  Alert threshold: score > 60, undesig.  │
└────────────────────┬────────────────────┘
                     │
         ┌───────────┴───────────┐
         │                       │
┌────────▼────────┐   ┌──────────▼──────────┐
│  Daily Digest   │   │    Query API         │
│  Markdown/HTML  │   │  GET /vessel/{imo}   │
│  GitHub publish │   │  GET /alerts/today   │
│  Telegram post  │   │  GET /fleet/summary  │
└─────────────────┘   └─────────────────────┘
```

---

## Roadmap

### v0.1 — Proof of concept
- [ ] OFAC SDN list ingestion and parsing (IMO number extraction)
- [ ] OpenSanctions vessel data ingestion
- [ ] TankerTrackers sanctioned list sync
- [ ] Basic vessel state store (SQLite)
- [ ] Manual vessel lookup by IMO number

### v0.2 — AIS integration
- [ ] MarineTraffic / AISHub live position polling
- [ ] AIS dark event detection (position gap > 6 hours)
- [ ] Russian port call detection (Primorsk, Ust-Luga, Novorossiysk, Kavkaz)
- [ ] Basic evasion score calculation

### v0.3 — Alert pipeline
- [ ] Flag change detection vs. historical registry data
- [ ] Vessels scoring >60, not yet designated → alert list
- [ ] Daily digest generation (Markdown)
- [ ] GitHub Pages auto-publish of daily digest

### v0.4 — Output & distribution
- [ ] FastAPI query endpoint
- [ ] Telegram bot for digest distribution
- [ ] CSV export for journalists
- [ ] Integration with OSINTukraine archiving pipeline

### v1.0 — Production
- [ ] PostgreSQL migration
- [ ] STS transfer detection (two vessels in proximity, both going dark)
- [ ] Ownership change tracking via IMO GISIS
- [ ] Historical trend analysis
- [ ] Referral pipeline to OSINT for Ukraine / EU enforcement contacts

---

## Who Uses This

**Primary users:**
- Investigative journalists (RFE/RL, Reuters, Bloomberg) covering Russia sanctions
- OSINT organizations (OSINT for Ukraine, Bellingcat, C4ADS)
- EU/Baltic coast guard enforcement teams
- NGOs tracking Russia war financing (CREA, KSE)

**How it feeds the advocacy track:**
The April 11, 2026 GL 134A waiver expiration is a specific policy moment. A tool that can show "here are 15 Russian shadow fleet vessels that moved through the Baltic in the past 30 days while the waiver was active" gives journalists and Senate staffers concrete, sourced data rather than estimates. That's the difference between a story and a press release.

---

## Related Projects

| Project | What it does | Link |
|---|---|---|
| **TankerTrackers** | Commercial; satellite + AIS tracking of sanctioned tankers | [tankertrackers.com](https://tankertrackers.com) |
| **OpenSanctions** | Aggregated sanctions database, free non-commercial API | [opensanctions.org](https://opensanctions.org) |
| **OSINT for Ukraine** | War crimes investigation tools | [osintforukraine.com](https://osintforukraine.com) |
| **OSINTukraine** | Telegram archiving/translation | [osintukraine.com](https://osintukraine.com) |
| **CREA** | Russia oil revenue tracking | [energyandcleanair.org](https://energyandcleanair.org) |

---

## Contributing

Solo project, LLM-assisted development (Claude Sonnet 4.6 + VSCode).

If you want to contribute:
1. Open an issue describing what you want to add
2. Priority areas: AIS data source integrations, evasion scoring refinements, output formatting
3. Stack is intentionally boring — plain Python, no exotic dependencies

---

## Context

Built March 2026 during active monitoring of the Russia-Ukraine war and Operation Epic Fury (US-Israel vs Iran). The GL 134/134A sanctions waiver — allowing India to purchase sanctioned Russian oil — demonstrated that manual monitoring of shadow fleet activity has direct policy relevance. The Anatoly Kolodkin incident (Russian tanker detected heading to Cuba using AIS spoofing, triggering GL 134A amendment) is exactly the kind of event this tool is designed to catch systematically rather than accidentally.

Russia earns approximately €510M/day from oil and LNG exports (CREA, March 2026). The shadow fleet is the mechanism. Public, open, continuous monitoring is the counter.
