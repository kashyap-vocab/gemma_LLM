# LTFS Survey Voice Bot

An outbound AI voice agent for **L&T Finance** that calls customers over PSTN, conducts a payment-feedback survey in **Devanagari Hindi**, and persists structured results to a database.

---

## Architecture Overview

```
SmartFlo PSTN ──WebSocket──► smartflo_bridge  ──LiveKit room──► LiveKit Agent (Priya)
                                                                      │
                                              ┌───────────────────────┤
                                              │                       │
                                        Deepgram STT          Local Gemma LLM
                                        (Hindi ASR)           (Gemma 2 9B, vLLM)
                                              │                       │
                                        Slot Extractor          Custom TTS
                                        (regex-based)          (Hindi voice)
                                              │
                                        PostgreSQL (Neon)
                                    (feedback + transcripts)
```

### Key components

| Path | Role |
|---|---|
| `agent/web_rtc_server.py` | LiveKit worker entrypoint — connects STT/LLM/TTS, drives the session lifecycle |
| `agent/survey_agent.py` | `SurveyAssistant` — Priya's persona, system prompt, tool functions |
| `agent/custom_llm.py` | HTTP wrapper for the local Gemma 2 9B vLLM server |
| `agent/custom_tts.py` | Custom TTS plugin (Hindi synthesis) |
| `agent/slot_extractor.py` | Regex-based extractor that fills survey slots from every user utterance |
| `agent/db_storage.py` | In-memory session state + flush to DB on call end |
| `smart-flo/smartflow_bridge.py` | WebSocket bridge: SmartFlo PSTN audio ↔ LiveKit room audio |
| `api/main.py` | FastAPI server — REST API + WebSocket bridge + React frontend |
| `api/auto_dialer.py` | Concurrent outbound call orchestrator (up to 5 parallel calls) |
| `api/customer_api.py` | CRUD for customer records and call results |
| `api/feedback_backfill_scheduler.py` | Periodic job to backfill unstructured feedback via LLM |
| `db/` | SQLAlchemy models: `Customer`, `CallMetadata`, `Conversation`, `CustomerFeedback` |
| `frontend/` | React + Tailwind dashboard (built with Vite) |
| `scripts/backfill_feedback_with_llm.py` | One-shot backfill script |

---

## Call Flow

1. **Auto-dialer** picks pending customers from DB and triggers SmartFlo outbound call.
2. **SmartFlo bridge** joins the LiveKit room as a participant, forwarding PSTN ↔ WebRTC audio.
3. **LiveKit agent** (`Priya`) waits for the bridge to connect, then plays the greeting.
4. **Each user utterance** goes through Deepgram STT → slot extractor (updates DB session) → Gemma LLM (generates next Hindi response) → custom TTS.
5. When all survey fields are collected, Priya reads a summary, confirms, then calls `end_call`.
6. On disconnect: session is flushed to `Conversation` + `CustomerFeedback` tables.

### Survey data collected

| Field | Values |
|---|---|
| Identity | CONFIRMED / NOT_AVAILABLE / REFUSED / SENSITIVE |
| Loan confirmed | YES / NO |
| Last month payment | YES / NO |
| Payee | self / relative / friend / third_party |
| Payment date | dd-mm-yyyy / cant_recall |
| Payment mode | upi / online / cash / branch / field_executive / nach / other |
| Payment reason | emi / settlement / foreclosure / other |
| Payment amount | number |

---

## Prerequisites

- Python 3.11+
- Node.js 18+ (for frontend)
- PostgreSQL database (Neon or local)
- LiveKit Cloud account
- Deepgram API key
- SmartFlo account (Tata Teleservices)
- Local Gemma 2 9B server running via vLLM (OpenAI-compatible API)
- Custom TTS server

---

## Setup

### 1. Clone and create virtualenv

```bash
git clone <repo>
cd Livekit_Flow
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure environment

Copy `.env.sample` to `.env` (or edit `.env` directly) and fill in:

```env
# LiveKit
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=...
LIVEKIT_API_SECRET=...

# Deepgram STT
DEEPGRAM_API_KEY=...

# SmartFlo telephony
SMARTFLO_API_KEY=...
SMARTFLO_API_URL=https://api-smartflo.tatateleservices.com/v1/click_to_call_support
SMARTFLO_FROM_NUMBER=91XXXXXXXXXX
SMARTFLO_AGENT_NUMBER=91XXXXXXXXXX

# Database (PostgreSQL)
DATABASE_URL=postgresql://user:pass@host/dbname?sslmode=require

# Local LLM (vLLM / Gemma 2 9B)
LOCAL_LLM_URL=http://<gpu-server>:7000
LOCAL_LLM_MODEL=google/gemma-2-9b-it

# Custom TTS
CUSTOM_TTS_URL=http://<tts-server>:7000/synthesize
CUSTOM_TTS_SAMPLE_RATE=22050

# Tuning (optional — defaults shown)
MIN_ENDPOINTING_DELAY=0.60
MAX_ENDPOINTING_DELAY=1.80
HANGUP_FLUSH_DELAY=1.0
```

### 3. Initialise the database

```bash
python -c "from db.database import engine, Base; from db.models import *; Base.metadata.create_all(bind=engine)"
```

### 4. Build the frontend

```bash
cd frontend
npm install
npm run build
cd ..
```

---

## Running

### Quick start (all services at once)

```bash
./start.sh              # agent + API server + frontend
./start.sh --ngrok      # also opens an ngrok tunnel and prints the SmartFlo WebSocket URL
```

### Manual (individual services)

```bash
# Terminal 1 — LiveKit agent
python agent/web_rtc_server.py dev

# Terminal 2 — FastAPI server (API + bridge + frontend)
python api/main.py
```

The dashboard is served at `http://localhost:8000`.

### Console mode (local testing, no telephony)

```bash
python agent/web_rtc_server.py console
```

Set `DEV_CUSTOMER_NAME` in `.env` to control the test customer name (default: `kashyap`).

---

## Docker

```bash
# Agent container
docker build -f Dockerfile -t ltfs-agent .

# Full stack (agent + bridge API)
docker compose up
```

Images used by `docker-compose.yml`:
- `AGENT_IMAGE` — LiveKit agent (default: `somashaker23/ltfs-agent-pl:latest`)
- `BRIDGE_IMAGE` — SmartFlo bridge + API (default: `somashaker23/smartflo-bridge:latest`)

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/customers` | List customers |
| `POST` | `/customers` | Add / import customers |
| `GET` | `/customers/{id}/feedback` | Get survey results for a customer |
| `POST` | `/auto-dialer/start` | Start outbound dialling campaign |
| `POST` | `/auto-dialer/stop` | Stop campaign |
| `GET` | `/auto-dialer/status` | Live dialler status (SSE stream) |
| `WS` | `/smartflo/stream` | SmartFlo PSTN audio bridge |

---

## Call Lifecycle & Timeouts

| Event | Timeout | Env var |
|---|---|---|
| Wait for customer to answer (bridge connect) | 120 s | — |
| Wait for closing TTS to finish after end_call | 30 s | — |
| Audio flush before WebSocket drop | 1 s | `HANGUP_FLUSH_DELAY` |

There is no maximum call duration limit by default. To add one, set a task that calls `_signal_hangup()` after N seconds in `web_rtc_server.py`.

---

## Project Structure

```
Livekit_Flow/
├── agent/
│   ├── web_rtc_server.py      # LiveKit worker entrypoint
│   ├── survey_agent.py        # Priya persona + system prompt + tools
│   ├── custom_llm.py          # Local Gemma 2 9B HTTP wrapper
│   ├── custom_tts.py          # Custom Hindi TTS plugin
│   ├── slot_extractor.py      # Regex survey-slot extractor
│   ├── db_storage.py          # In-memory session + DB flush
│   └── metrics.py             # Per-turn latency tracker
├── api/
│   ├── main.py                # FastAPI app (API + bridge + frontend)
│   ├── customer_api.py        # Customer CRUD routes
│   ├── auto_dialer.py         # Outbound call orchestrator
│   ├── smartflo_client.py     # SmartFlo HTTP client
│   └── feedback_backfill_scheduler.py
├── db/
│   ├── database.py            # SQLAlchemy engine + session
│   ├── models/                # ORM models
│   │   ├── customer.py
│   │   ├── call_metadata.py
│   │   ├── conversation.py
│   │   └── customer_feedback.py
│   └── utils.py
├── smart-flo/
│   └── smartflow_bridge.py    # PSTN ↔ LiveKit WebSocket bridge
├── frontend/                  # React + Tailwind dashboard
│   └── src/
├── scripts/
│   └── backfill_feedback_with_llm.py
├── docker-compose.yml
├── Dockerfile
├── smartflo.Dockerfile
├── requirements.txt
├── start.sh                   # One-command launcher
└── .env
```

---

## CI / CD

GitHub Actions workflows are in `.github/workflows/`:

| Workflow | Trigger | What it does |
|---|---|---|
| `ltfs-agent-pl-build.yml` | Push to `main` | Builds + pushes agent Docker image |
| `smartflo-workflow.yml` | Push to `main` | Builds + pushes bridge Docker image |
| `bug-agent-build.yml` | Manual / PR | Agent build for bug-fix branches |
| `bug-smartflo-build.yml` | Manual / PR | Bridge build for bug-fix branches |
