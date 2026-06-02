# Airspace Snapshot Analyst

A three-agent local AI/reporting system that fetches live ADS-B flight data over the US West Coast corridor (LAX / SFO / SEA), 
stores it in SQLite, and generates a polished Airspace Situation Report as a Word document.  The first agent uses a local LLM for analysis; 
the second agent is a deterministic reporting agent that converts structured JSON into a formatted `.docx` report;
the third agent compares successive snapshots to identify traffic trends over time.

https://opensky-network.org/
https://openskynetwork.github.io/opensky-api/index.html#


Runs entirely on your unix/linux based system — no API key required, nothing leaves your machine
except the OpenSky Network fetch.

This project was developed using AI-assisted coding workflows with Claude and local LLM experimentation.
It is for learning and portfolio demonstration only. It is not intended for operational flight safety, dispatch, air traffic control, or regulatory use.

## Motivation

This project explores how local LLMs can support aviation operations analysis by separating deterministic data ingestion from AI-assisted interpretation. 
The goal is not real-time air traffic control, but a local prototype for turning public ADS-B snapshots into structured operational reports.  


## Architecture

```
OpenSky Network API (free, no auth)
    │
    │  HTTP GET /states/all?bbox=US_WEST_COAST
    ▼  deterministic — no LLM
┌─────────────────────────────┐
│  Fetcher (tools/fetcher.py)  │
│  JSON → SQLite               │
│  Unit conversions (m→ft etc) │
│  Anomaly thresholds applied  │
└──────────────┬──────────────┘
               │
          airspace.db
               │
    ┌──────────▼──────────────────┐
    │  Agent 1: Analyst            │  ← Gemma 4 4B via llama.cpp (auto)
    │  Pre-computed stats from DB  │
    │  → pattern detection         │
    │  → anomaly classification    │
    │  → structured JSON           │
    └──────────┬──────────────────┘
               │
    ┌──────────▼──────────────────────┐
    │  Agent 3: Trend Analyst          │  ← Gemma 4 4B
    │  Compares current vs previous    │
    │  → traffic deltas                │
    │  → anomaly changes over time     │
    │  → trend observations            │
    └──────────┬──────────────────────┘
               │
    ┌──────────▼──────────────────┐
    │  Agent 2: Report Writer      │  ← Node.js (no LLM)
    │  JSON → .docx report         │
    │  Status banner, tables,      │
    │  anomaly list, country chart │
    │  trend analysis section      │
    └──────────┬──────────────────┘
               │
           reports/
```

**Key principle:** the LLM only runs on the read path. Fetching and
storing is purely deterministic — fast regardless of snapshot size.

## Output

A formatted Word document containing:
- Status banner (NORMAL / ELEVATED / CRITICAL) with colour coding
- Emergency squawk alert table (if any 7500/7600/7700 detected)
- Traffic summary prose
- Snapshot statistics KPI table
- Detected anomalies table (type / severity / description)
- Notable patterns (bullets)
- **Trend Analysis** — compares current vs previous snapshot with traffic deltas, anomaly changes, and trend observations (if multiple snapshots exist)
- Analyst notes
- Origin country breakdown table (page 2)

Plus an interactive HTML aircraft position map (`reports/aircraft_map_snapshot_<id>.html`)
with altitude-coloured markers, emergency alerts, and clickable popups showing
callsign, registration, type, operator, speed, and altitude.

## Prerequisites

### 1. llama.cpp + Gemma 4 4B

Download a Gemma 4 4B GGUF (e.g. from lmstudio-community). Create a `.env` file
in the project root and point it to your model:

```env
# ── Required: path to your downloaded GGUF model file ──
MODEL_PATH=/Users/you/Downloads/gemma-4-E4B-it-Q8_0.gguf

# Optional overrides (defaults shown):
# LLAMA_BIN=/usr/local/bin/llama-server-mcp
# LLAMA_PORT=8082
# LLAMA_CTX_SIZE=8192
# LLAMA_THREADS=6
```

The server is launched automatically when you run `orchestrator.py` — no
manual setup needed. It shuts down when Python exits.
The optional `gemma4_official.jinja` chat template file in the project root is
used if present; without it the model's built-in template is used instead.

### 2. Python virtual environment
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Node.js + docx
```bash
brew install node
cd airspace_analyst
npm install           # installs docx from package.json
```

## Usage

Make sure your virtual environment is activated:
```bash
source .venv/bin/activate
```

### Full pipeline (fetch → analyse → report)
```bash
python orchestrator.py snapshot
```
This produces three files in `reports/`:
- `airspace_report_<timestamp>.docx` — formatted Word report (includes Trend Analysis if ≥ 2 snapshots exist)
- `aircraft_map_snapshot_<id>.html` — interactive aircraft position map
- `manifest_snapshot_<id>.html` — full aircraft list table with positions

### Fetch and store only (no LLM, instant)
```bash
python orchestrator.py fetch
```

### Generate report from a stored snapshot
```bash
python orchestrator.py list           # find the snapshot id
python orchestrator.py report --id 3
```

### List all stored snapshots
```bash
python orchestrator.py list
```

### Generate interactive aircraft position map
```bash
python -m tools.mapper                         # latest snapshot
python -m tools.mapper --id 2                  # specific snapshot
python -m tools.mapper --callsign UAL949       # track one aircraft across snapshots
python -m tools.mapper --registration N78017   # track by tail number
python -m tools.mapper --find --callsign DAL   # search available callsigns
```

### List airborne aircraft with positions
```bash
python -m tools.manifest                       # all airborne, latest snapshot
python -m tools.manifest --id 2                # specific snapshot
python -m tools.manifest --callsign UAL        # filter by airline callsign prefix
python -m tools.manifest --min-alt 30000       # only high-altitude aircraft
python -m tools.manifest --emergency           # only emergency squawks
python -m tools.manifest --html                # interactive HTML table
python -m tools.manifest --html manifest.html  # custom output path
python -m tools.manifest --json               # JSON output for programmatic use
```

## Project Structure

```
airspace_analyst/
├── orchestrator.py              # entry point
├── agents/
│   ├── analyst.py               # Agent 1: DB summary → Gemma 4 4B → JSON
│   ├── trend_analyst.py         # Agent 3: current vs previous snapshot comparison
│   └── report_writer.py         # Agent 2: JSON → .docx + map + manifest
├── tools/
│   ├── db.py                    # SQLite ingestor + summary builder
│   ├── download_aircraft_db.py  # OpenSky aircraft registry downloader
│   ├── fetcher.py               # OpenSky Network HTTP client
│   ├── llm.py                   # llama.cpp server manager + client
│   ├── manifest.py              # Airborne aircraft list with positions
│   ├── mapper.py                # Interactive HTML aircraft position map
│   └── write_report.js          # docx report writer (Node.js)
├── data/                        # downloaded aircraft DB CSV (gitignored)
├── skills/
│   └── SKILL.md                 # OpenSky API conventions (used by tools/db.py, tools/fetcher.py)
├── prompts/
│   ├── analyst_system.txt       # analyst extraction prompt
│   └── trend_analyst_system.txt # trend comparison prompt
├── reports/                     # generated .docx reports + maps + manifests
├── airspace.db                  # SQLite — created on first run
├── package.json
└── requirements.txt
```

## Memory Profile (M4 Mac Mini 24GB)

| Component               | RAM       |
|-------------------------|-----------|
| Gemma 4 4B Q8 (llama.cpp)| ~4–6 GB   |
| Python + SQLite         | < 100 MB  |
| Node.js (report writer) | < 100 MB  |
| **Peak total**          | **~4 GB** |

## OpenSky Network Notes

- Free, no account or API key required
- Rate limit: 1 request per 10 seconds (anonymous)
- Coverage depends on volunteer ADS-B receivers; some gaps in remote areas
- Data lags ~5–15 seconds behind reality
- More info: https://opensky-network.org/apidoc/

## Troubleshooting

**`Cannot reach llama-server at localhost:8082`**
→ Check `MODEL_PATH` in `.env` points to a valid GGUF file. The server
  auto-starts when you run `python orchestrator.py snapshot`.

**Trend Analysis section is missing from report**
→ Normal on the very first run. You need at least 2 snapshots in the DB
  for a comparison. Run `python orchestrator.py snapshot` again.

**`Cannot find module 'docx'`**
→ Run `npm install` inside the `airspace_analyst/` directory.

**HTTP 429 from OpenSky**
→ Rate limited. The fetcher waits 12 seconds and retries automatically.

**HTTP 503 from OpenSky**
→ OpenSky is occasionally unavailable. Try again in a few minutes.

**No aircraft returned**
→ Rare but possible if OpenSky coverage is temporarily down.
   Try `python orchestrator.py fetch` again after a minute.
