# AutoRCA — Automated Log Monitoring & Error Analysis System

A production-ready service that **continuously watches a folder for error log
files, automatically detects new/changed files, and performs AI-assisted
root-cause analysis (RCA)** — producing a detailed report for every error it
finds, then marking the file processed so it's never analyzed twice.

It runs indefinitely, recovers gracefully after a restart, and uses a **free**
AI model (Google Gemini free tier) with an offline rule-based fallback so it
keeps working even with no API key or internet.

---

## ✨ What it does

For every detected error log, AutoRCA produces a Markdown report explaining:

- **What happened** · **Why it happened** · **Impact**
- **Root cause** (follows the exception `Caused by:` chain to the deepest cause)
- **Recommended resolution steps**
- **Confidence level** of the analysis

It also identifies the **error type, exception class, affected component,
failure point, probable trigger**, reconstructs the **sequence of events**, and
detects **cascading / secondary failures**.

**Designed to be understood at a glance:**

- 🧭 **Where is the issue?** Every analysis is pinned to one of four levels —
  **Application · Server · Database · Network** — shown as a visual, so you
  instantly know whose problem it is.
- 💬 **Plain-English explanation** — a one-line, non-technical summary
  ("…like trying to use an expired ID card to get into a building") alongside
  the technical detail.
- 🔁 **Request & Response bodies** — the actual HTTP request/response captured
  from the log (with huge encrypted payloads trimmed) so you see exactly what
  came in and what went back.
- 🎯 **Every affected endpoint, grouped by reason** — when one log contains many
  failing endpoints, each is detected as an incident (with how many times it
  occurred). Endpoints that failed for the **same reason** are explained once;
  **different reasons** get their own separate explanation.
- 🌙/☀️ **Dark & light mode** — toggle in the top bar; your choice is remembered.

See [`reports/SAMPLE_RCA_apigw_error_log.md`](reports/SAMPLE_RCA_apigw_error_log.md)
for real output generated from the included API-gateway log.

---

## 🚀 Quick start

```powershell
# 1. Install dependencies
python -m pip install -r requirements.txt

# 2. (Optional but recommended) enable Gemini AI
#    Get a FREE key at https://aistudio.google.com/app/apikey
copy .env.example .env
#    then edit .env and paste your key into GEMINI_API_KEY

# 3. Start monitoring (runs forever; Ctrl+C to stop)
python main.py
```

Drop any `.log` / `.txt` / `.out` / `.trace` file into the **`Error Log File/`**
folder and a report appears in **`reports/`** within seconds. The processed log
is moved into **`Error Log File/processed/`**.

> **No API key?** It still works — it automatically falls back to the built-in
> offline heuristic engine. Add the key any time to upgrade to full AI analysis.

---

## 🖥️ Web Portal

A polished dashboard for browsing the analyses. Run it **alongside** the monitor:

```powershell
python webapp.py            # open http://localhost:5000
python webapp.py --port 8080
python webapp.py --host 0.0.0.0   # expose on your network
```

The portal gives you:

- **Dashboard** — live stat cards (total / successful / failed / high-confidence),
  plus breakdowns by **category** and **confidence**, and the most recent analyses.
  Auto-refreshes so new results appear on their own.
- **All Reports** — a searchable list (by file name, error type, category, summary).
- **Report detail** — an at-a-glance view: the problem in plain English, the
  **level** (App/Server/DB/Network), every **affected endpoint grouped by reason**
  with its request/response, what/why/impact, fix steps, timeline, and a one-click
  **Download Markdown** button.
- **Dark / light theme** toggle (🌙/☀️) in the top bar.

It is read-only over the same `autorca.db`, so it's always in sync with the monitor.

---

## 🕹️ Usage / CLI

```powershell
python main.py                 # continuous monitoring (default)
python main.py --once          # process current files once, then exit
python main.py --file PATH     # analyze a single file and exit
python main.py --stats         # show processing history and exit
python main.py --config PATH   # use an alternate config file
```

---

## ⚙️ Configuration — `config.yaml`

| Setting | Meaning | Default |
|---------|---------|---------|
| `monitoring.watch_dir` | folder to watch | `Error Log File` |
| `monitoring.recursive` | watch sub-folders | `true` |
| `monitoring.extensions` | file types analyzed | `.log .txt .out .trace` |
| `monitoring.stability_seconds` | wait for a file to finish writing | `2` |
| `monitoring.poll_interval_seconds` | how often the folder is re-checked | `30` |
| `processing.processed_subdir` | where processed files are moved | `processed` |
| `processing.reports_dir` | where reports are written | `reports` |
| `processing.db_path` | history database | `autorca.db` |
| `processing.max_excerpt_chars` | max log size sent to AI | `16000` |
| `ai.provider` | `gemini` or `heuristic` | `gemini` |
| `ai.model` | Gemini model id | `gemini-2.0-flash` |
| `ai.fallback_to_heuristic` | fall back if AI fails | `true` |

Secrets (the API key) live in **`.env`**, never in `config.yaml`.

---

## 🧠 How it works

```
 Error Log File/  ──(watchdog events + periodic rescan)──►  detect new/changed file
        │
        ▼
   wait until file is STABLE (size unchanged)  ──►  hash file (sha256)
        │
        ▼
   already in history DB?  ──yes──►  skip (move aside)
        │ no
        ▼
   parse  ──►  ERROR DIGEST  (exceptions, caused-by chain, components,
        │                      correlation ids, HTTP events, key stack frames)
        ▼
   ANALYZE  ──►  Gemini (free)  ──or fallback──►  offline heuristic engine
        │
        ▼
   render Markdown report  ──►  reports/RCA_<file>_<timestamp>.md
        │
        ▼
   record in history DB  ──►  move log to Error Log File/processed/
```

**Key design points**

- **Restart-safe:** on startup it rescans the folder and re-queues anything not
  in the history database, so nothing is lost across restarts/crashes.
- **No duplicate analysis:** files are keyed by **content hash**, so an identical
  file is skipped, while a genuinely *changed* file is treated as new work.
- **Never blocks on half-written files:** a file is only processed once its size
  has stopped changing (`stability_seconds`).
- **Token-efficient:** huge logs are distilled to an *error digest* before being
  sent to the AI, keeping requests small and free-tier-friendly.
- **Resilient:** a single bad file is logged, recorded as `failed`, moved aside,
  and the service keeps running — it never crash-loops.

---

## 📁 Project layout

```
AutoRCA/
├─ main.py                 # CLI entrypoint (the monitor)
├─ webapp.py               # web portal entrypoint
├─ config.yaml             # all settings
├─ .env.example            # template for GEMINI_API_KEY
├─ requirements.txt
├─ Error Log File/         # ◄── drop logs here  (watched)
│  └─ processed/           #      processed logs moved here
├─ reports/                # generated RCA reports
└─ autorca/
   ├─ config.py            # config + .env loader
   ├─ database.py          # SQLite processing history + structured analyses
   ├─ log_parser.py        # raw log -> error digest
   ├─ processor.py         # single-file pipeline
   ├─ service.py           # watchdog monitor + rescan loop
   ├─ reporter.py          # digest+analysis -> Markdown report
   ├─ providers/
   │  ├─ base.py           # provider interface + AnalysisResult
   │  ├─ gemini_provider.py    # Google Gemini (free) via REST
   │  └─ heuristic_provider.py # offline rule engine / fallback
   └─ web/                 # Flask portal (templates + static + routes)
```

---

## 🔌 Swapping AI providers

The engine is pluggable. To add another free backend (e.g. Groq, Ollama),
create a class in `autorca/providers/` that subclasses `AnalysisProvider`,
returns an `AnalysisResult`, and register it in
`autorca/providers/__init__.py::build_provider`. No other code changes needed.

---

## 🧩 Running it as an always-on service

- **Quick:** leave `python main.py` running in a terminal — it monitors forever.
- **Windows background:** wrap it with [NSSM](https://nssm.cc/) to install it as
  a Windows Service, or create a Task Scheduler task with trigger *At startup*.
- **Auto-restart:** because state lives in SQLite, restarting the process is safe
  — it resumes exactly where it left off.
```

---
*Free AI: Google Gemini · Offline fallback: built-in heuristic engine · No paid services required.*
