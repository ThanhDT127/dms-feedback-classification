# DMS Service Architecture & Developer Guide

This folder contains the core backend services and frontend dashboard of the DMS Feedback Classification system, containerized with Docker.

## 1. System Architecture

The service consists of two primary Docker containers running concurrently:

```text
               +-------------------------------------------------+
               |                   SharePoint                    |
               +---^-----------------^---------------------^-----+
                   | Input/          | Output/             | Check_Point/
                   | (xlsx)          | (output xlsx)       | (state json)
                   v                 v                     v
        +----------+-----------------+---------------------+----------+
        |                                                             |
        |  Docker Compose environment (VM Host)                       |
        |                                                             |
        |   +--------------------------+  Shared State/Logs  +-----+  |
        |   |   dms-feedback-watcher   |<===================>| Web |  |
        |   |  (Python Watcher Daemon) |   (work/ directory) |  UI |  |
        |   +--------------------------+                     +--^--+  |
        |                                                       |     |
        +-------------------------------------------------------|-----+
                                                                | port 8501
                                                                |
                                                          +-----v-----+
                                                          |  Browser  |
                                                          +-----------+
```

### 1.1. Watcher Service (`dms-feedback-watcher`)
- **Command:** `python -m dms`
- **Responsibility:** Runs as a background daemon.
  - Periodically polls SharePoint `Input/` folder.
  - Downloads new workbooks to `work/input/`.
  - Executes the classification pipeline (Baseline Model -> Keyword Matcher -> Gemini Refinement).
  - Generates output workbooks and uploads them to SharePoint `Output/`.
  - Saves progress checkpoints dynamically (every N rows) to SharePoint `Check_Point/` to support recovery from interruptions.
  - Logs state updates and operational logs locally.

### 1.2. Web Service (`dms-feedback-web`)
- **Command:** `python -m dms.web`
- **Responsibility:** Exposes a FastAPI server on port `8501` to power the Web Dashboard.
  - Serves static assets (HTML/CSS/JS) for the frontend SPA dashboard.
  - Manages configuration settings in the `.env` file via `api/settings`.
  - Exposes APIs to view system logs (`api/logs`), health checks (`api/health`), and metrics (`api/metrics`).
  - Supports manual batch file classification triggering (`api/classify`) and file synchronization/uploads (`api/files`).
  - Streamlines system logs via WebSockets (`/ws/logs`) and batch progress updates (`/ws/progress`).

---

## 2. Directory & Package Structure

```text
service/
  src/dms/                     # Main Python Package
    pipeline/                  # Classification pipeline
      baseline_model.py        # Baseline classification model
      keyword_matcher.py       # Catalog & keyword lookup
      issue_classifier.py      # Vertex AI/Gemini prompt orchestrator
      runner.py                # Pipeline execution wrapper
    web/                       # FastAPI Web Server
      api/                     # REST Endpoints
        classify.py            # Manual batch processing endpoints
        files.py               # File uploads and sync triggers
        metrics_api.py         # Health checks, charts, logs APIs
        pipeline_api.py        # Keyword catalogs & prompt test APIs
        settings_api.py        # Configuration verification & saving
      ws/                      # WebSocket routers (progress/logs)
      app.py                   # FastAPI Application Factory
      deps.py                  # Dependency injection container
    settings.py                # Pydantic settings loading & parsing
    watcher.py                 # SharePoint polling loop daemon
  static/                      # Frontend SPA Dashboard (Vanilla JS)
    css/style.css              # Custom styling
    js/
      components/              # Reusable UI parts (sidebar, logs)
      pages/                   # Page components (dashboard, classify, metrics, qa, settings)
      api.js                   # API Client module
      app.js                   # Client Router and Initialization
    index.html                 # Main entry point
  scripts/                     # Operations scripts
    reconstruct_history.py     # Re-syncs stats history from SharePoint
  docker-compose.yml           # Multi-container orchestrator
  Dockerfile                   # Shared service container image
```

---

## 3. Web Dashboard Components

The Vanilla JS Single Page Application (SPA) contains 7 active pages:
1. **Tổng quan (Dashboard - `dashboard.js`)**: Displays host CPU/Memory metrics, service health status, active environment info, and live system log streams.
2. **Phân loại (Classify - `classify.js`)**: Starts and monitors manual batch classification jobs, with real-time progress bars and downloadable Excel results.
3. **Quản lý file (Files - `files.js`)**: Lists local and SharePoint files, uploads new files to local staging, and triggers file sync routines.
4. **Thống kê (Metrics - `metrics.js`)**: Renders daily file count bar charts and category distribution doughnut charts.
5. **Cấu hình Pipeline (Pipeline - `pipeline.js`)**: Displays the active keyword taxonomy and local classification models.
6. **Hỏi đáp / Thử nghiệm (Q&A - `qa.js`)**: A sandbox playground to test individual feedback entries against the current model and keyword configurations.
7. **Cấu hình hệ thống (Settings - `settings.js`)**: Visual editor for all configurations in `.env` (Azure, SharePoint, Gemini model parameters).

---

## 4. Run/Debug Commands

### Starting the services
```bash
# Set up env configuration
copy .env.example .env

# Build and start services in background
docker compose up -d --build

# View container logs
docker compose logs -f
```

### Accessing the Web UI
The UI is available on port `8501`:
`http://localhost:8501/`

### Running the history reconstruction script
If the database dashboard metrics are out of sync or empty, run the reconstruction utility:
```bash
docker compose exec watcher python scripts/reconstruct_history.py
```

### Checking API docs
FastAPI automatically serves Interactive OpenAPI documentation at:
`http://localhost:8501/docs`

