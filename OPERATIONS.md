# 📘 System Overview & Operations Guide

> **DMS Feedback Classification Service**
> Comprehensive technical documentation for deployment, operation, and maintenance.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Architecture Deep Dive](#2-architecture-deep-dive)
3. [Data Flow](#3-data-flow)
4. [Module Reference](#4-module-reference)
5. [Deployment Guide](#5-deployment-guide)
6. [Configuration Reference](#6-configuration-reference)
7. [Monitoring & Observability](#7-monitoring--observability)
8. [Troubleshooting](#8-troubleshooting)
9. [Maintenance Procedures](#9-maintenance-procedures)
10. [Security Model](#10-security-model)

---

## 1. System Overview

### 1.1 Purpose

The DMS Feedback Classification Service automates the classification of customer feedback received through internal Excel files stored in a SharePoint document library. It uses Google Gemini LLM to:

- **Classify products** — Maps free-text feedback to a 3-level product hierarchy (L1 → L2 → L3) using RAG-enhanced prompts
- **Classify issues** — Determines issue type (quality, delivery, service, etc.) using structured LLM prompts

### 1.2 Business Context

```
Customer Feedback (Excel) → SharePoint Input/ → Service → Classified Output → SharePoint Output/
                                                                              ↓
                                                                         Email Notification
```

Each Excel file contains rows with feedback text in the `"Nội dung vấn đề"` column. The service enriches each row with:

| Output Column | Description |
|---------------|-------------|
| `Nhóm SP (Cấp 1)` | Product group level 1 |
| `Loại SP (Cấp 2)` | Product type level 2 |
| `Mã SP (Cấp 3)` | Product code level 3 |
| `Phân loại vấn đề` | Issue classification |
| `Chi tiết vấn đề` | Issue detail |

### 1.3 Technology Stack

| Component | Technology | Version |
|-----------|-----------|---------|
| Runtime | Python | 3.11 |
| LLM | Google Gemini via Vertex AI | gemini-2.5-flash-lite |
| Auth | MSAL (Microsoft) + GCP SA | Client Credentials |
| Storage | SharePoint Online (Graph API) | v1.0 |
| Notification | Microsoft Graph Mail API | v1.0 |
| Container | Docker + Docker Compose | 3.x |
| Logging | Python logging + JSON Lines | RotatingFileHandler |

---

## 2. Architecture Deep Dive

### 2.1 Component Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                          Docker Container                        │
│                                                                  │
│  ┌──────────┐    ┌──────────┐    ┌───────────┐    ┌──────────┐  │
│  │ watcher  │───►│ pipeline │───►│ sharepoint│───►│ notify   │  │
│  │          │    │ /runner  │    │           │    │          │  │
│  │ (poll    │    │          │    │ (upload)  │    │ (email)  │  │
│  │  loop)   │    │ ┌───────┐│    └───────────┘    └──────────┘  │
│  └────┬─────┘    │ │  RAG  ││                                   │
│       │          │ │Product││    ┌───────────┐                   │
│       │          │ │Matcher││    │  Gemini   │                   │
│       │          │ └───┬───┘│    │  Client   │                   │
│       │          │     │    │    └─────┬─────┘                   │
│       │          │ ┌───▼───┐│          │                         │
│       │          │ │ Issue  ││──────────┘                        │
│       │          │ │Classif.││                                   │
│       │          │ └────────┘│                                   │
│       │          └───────────┘                                   │
│       │                                                          │
│  ┌────▼──────────────────────────────────────────────────────┐   │
│  │ Observability                                             │   │
│  │ ┌────────────┐  ┌────────────┐  ┌──────────────────────┐ │   │
│  │ │ metrics.py │  │ logging_   │  │ health.json          │ │   │
│  │ │            │  │ config.py  │  │ metrics.json         │ │   │
│  │ │ counters   │  │            │  │ daily-summary.jsonl  │ │   │
│  │ │ timers     │  │ JSON Lines │  │ dms-service.jsonl    │ │   │
│  │ └────────────┘  └────────────┘  └──────────────────────┘ │   │
│  └───────────────────────────────────────────────────────────┘   │
│                                                                  │
│  Volumes: ./work (state) │ ./logs (logs) │ ./Keyword (ref, ro)  │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 Module Responsibilities

| Module | File | Responsibility |
|--------|------|----------------|
| **Watcher** | `watcher.py` | Main loop: poll → download → process → upload → notify |
| **Config** | `config.py` | Centralized env var loading, path resolution |
| **Auth** | `auth.py` | MSAL Client Credentials + token caching |
| **SharePoint** | `sharepoint.py` | Graph API: list files, download, upload |
| **Notification** | `notification.py` | Graph API Mail.Send with HTML templates |
| **Gemini Client** | `gemini_client.py` | Vertex AI / API Key abstraction |
| **Pipeline Runner** | `pipeline/runner.py` | Batch orchestrator with checkpointing |
| **RAG Product** | `pipeline/rag_product.py` | BM25 + fuzzy product matching |
| **Issue Classifier** | `pipeline/issue_classifier.py` | LLM issue type classification |
| **Excel Formatter** | `pipeline/excel_formatter.py` | Output Excel generation |
| **Logging** | `logging_config.py` | JSON Lines formatter + rotation |
| **Metrics** | `metrics.py` | Operational counters + health data |

---

## 3. Data Flow

### 3.1 Processing Pipeline (per file)

```
1. POLL      watcher.py         List SharePoint Input/ folder
                                Filter: new files not in seen_files.json
     │
2. DOWNLOAD  sharepoint.py      Download .xlsx to work/input/
     │
3. PARSE     runner.py          Read Excel, find text column
     │
4. BATCH     runner.py          Split rows into batches (default: 20)
     │
     ├── For each batch:
     │   │
     │   4a. RAG MATCH          rag_product.py
     │   │   BM25 keyword search → fuzzy match → top candidates
     │   │   Build context prompt with product hierarchy
     │   │
     │   4b. GEMINI CALL #1     gemini_client.py
     │   │   Product classification (L1/L2/L3)
     │   │
     │   4c. GEMINI CALL #2     gemini_client.py
     │   │   Issue classification
     │   │
     │   4d. CHECKPOINT         Save progress to work/checkpoint/
     │
5. FORMAT    excel_formatter.py Write output Excel with new columns
     │
6. UPLOAD    sharepoint.py      Upload to Output/ and Check_Point/
     │
7. NOTIFY    notification.py    Send email notification
     │
8. RECORD    watcher.py         Update seen_files.json → "done"
                                Record metrics (success/failure)
```

### 3.2 File State Machine

```
                  ┌─────────┐
    New file ────►│  (new)  │
                  └────┬────┘
                       │ download + process
                       ▼
                  ┌─────────┐     error     ┌─────────┐
                  │processing│─────────────►│  error  │
                  └────┬────┘               └────┬────┘
                       │ success                 │ auto-retry (≤3)
                       ▼                         ▼
                  ┌─────────┐              ┌─────────┐
                  │  done   │              │  retry  │
                  └─────────┘              └────┬────┘
                                                │ next poll
                                                ▼
                                           (re-process)
```

### 3.3 Retry Policy

| Parameter | Value | Description |
|-----------|-------|-------------|
| `MAX_FILE_RETRIES` | 3 | Max retry attempts per file |
| Retry trigger | Automatic | Failed files auto-set to `retry` status |
| Backoff | Next poll cycle | Retried on next polling interval |
| Final state | `error` | After 3 failures, marked as permanent error |

---

## 4. Module Reference

### 4.1 watcher.py — Main Orchestrator

**Entry point:** `python watcher.py`

**Key functions:**

| Function | Description |
|----------|-------------|
| `main_loop()` | Infinite polling loop with configurable interval |
| `poll_once(seen)` | Single poll cycle: list → filter → process |
| `_process_file(file, seen)` | Download → pipeline → upload → notify |
| `_update_health(cycle)` | Write enriched health.json |
| `_write_daily_summary()` | Generate daily metrics summary at midnight |

**Global state:**
- `metrics` — `MetricsCollector` instance (persisted to `work/metrics.json`)
- `seen` — File tracking dict (persisted to `work/seen_files.json`)

### 4.2 pipeline/runner.py — Batch Processor

**Key function:** `run_pipeline(input_path, output_path, checkpoint_path)`

**Batch processing:**
1. Reads input Excel file
2. Detects text column (`"Nội dung vấn đề"`)
3. Splits into batches of `LLM_BATCH_SIZE` rows (default: 20)
4. For each batch:
   - RAG product matching (BM25 + fuzzy)
   - Gemini product classification
   - Gemini issue classification
5. Saves checkpoint every `CKPT_EVERY` rows (default: 50)
6. Writes output Excel with classification columns

**Checkpoint format:** JSON with processed rows, timestamp, batch index.

### 4.3 pipeline/rag_product.py — RAG Product Matcher

**Algorithm:**
1. **Text preprocessing:** Unidecode normalization, Vietnamese tokenization
2. **BM25 search:** Keyword-based retrieval from product catalog
3. **Fuzzy matching:** RapidFuzz similarity scoring
4. **Context building:** Top-k candidates formatted as LLM context

**Product hierarchy:** L1 (Group) → L2 (Type) → L3 (Code)

### 4.4 pipeline/issue_classifier.py — Issue Classifier

**Classification categories:**
- Chất lượng sản phẩm (Product Quality)
- Dịch vụ (Service)
- Giao hàng (Delivery)
- Bảo hành (Warranty)
- Khác (Other)

**Method:** Structured prompt with few-shot examples sent to Gemini.

### 4.5 notification.py — Email Notifications

**Flow:**
1. Acquire token via MSAL Client Credentials
2. Build HTML email with processing summary
3. Send via Graph API `/users/{sender}/sendMail`

**Email content includes:**
- File name, row count, processing time
- Success/failure status
- Timestamp

---

## 5. Deployment Guide

### 5.1 Prerequisites

| Requirement | Details |
|-------------|---------|
| Docker | v20.10+ with Docker Compose v2 |
| Azure AD | App Registration with admin-consented permissions |
| GCP | Service Account with Vertex AI API enabled |
| Network | Outbound HTTPS to `graph.microsoft.com`, `aiplatform.googleapis.com` |

### 5.2 Azure AD Setup

1. **Create App Registration** in Azure Portal
2. **API Permissions** (Application type, admin consent required):
   - `Files.ReadWrite.All` — Read/write SharePoint files
   - `Mail.Send` — Send email notifications
   - `User.Read.All` — (Optional) User lookup for notifications
3. **Create Client Secret** and note the value
4. **Note:** Tenant ID, Client ID, Client Secret

### 5.3 GCP Setup

1. Create a **Service Account** in GCP Console
2. Enable **Vertex AI API**
3. Download the JSON key file
4. Place as `service/testvertex.json`

### 5.4 SharePoint Setup

1. Identify the **Document Library Drive ID** (use Graph Explorer)
2. Create folder structure:
   ```
   Phan_Loai_Phan_Hoi/
   ├── Input/         ← Drop .xlsx files here
   ├── Output/        ← Service writes results here
   └── Check_Point/   ← Service writes checkpoints here
   ```
3. Note the **Root Folder Item ID**

### 5.5 Deployment Steps

```bash
# 1. Clone repository
git clone https://github.com/YOUR_USERNAME/dms-feedback-classification.git
cd dms-feedback-classification/service

# 2. Configure environment
cp .env.example .env
nano .env  # Fill in all required values

# 3. Place data files
cp /path/to/product-catalog.xlsx Keyword/
cp /path/to/sa-key.json testvertex.json

# 4. Create work directories
mkdir -p work/input work/output work/checkpoint logs

# 5. Deploy
docker-compose up -d

# 6. Verify
docker logs -f dms-feedback-watcher
cat work/health.json | python3 -m json.tool
```

### 5.6 Updating

```bash
cd service
git pull origin main
docker-compose down
docker-compose up -d --build
```

---

## 6. Configuration Reference

### 6.1 Environment Variables

#### Azure AD (Required)

```env
AZURE_TENANT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
AZURE_CLIENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
AZURE_CLIENT_SECRET=your-client-secret-value
```

#### Gemini LLM (Required)

```env
# Backend: "vertex" (recommended) or "apikey"
GEMINI_BACKEND=vertex
GEMINI_MODEL=gemini-2.5-flash-lite

# Vertex AI settings
GCP_PROJECT_ID=your-gcp-project-id
GCP_LOCATION=global
GCP_SERVICE_ACCOUNT_JSON=/app/data/sa-key.json
```

#### SharePoint (Required)

```env
SHAREPOINT_DRIVE_ID=b!xxxxxxxxxxxxxxxxxxxxxxxx
SHAREPOINT_ROOT_FOLDER_ID=01XXXXXXXXXXXXXXXXXX
```

#### Polling & Processing

```env
POLL_INTERVAL_SECONDS=300     # 5 minutes (default)
LLM_BATCH_SIZE=20             # Rows per LLM call
CKPT_EVERY=50                 # Checkpoint save interval
```

#### Notification (Optional)

```env
NOTIFICATION_SENDER_EMAIL=sender@your-org.com
NOTIFICATION_RECIPIENTS=user1@example.com,user2@example.com
```

#### Docker-specific (set in docker-compose.yml)

```env
DATA_DIR=/app/data
WORK_DIR=/app/data/work
LOG_DIR=/app/data/logs
GOOGLE_APPLICATION_CREDENTIALS=/app/data/sa-key.json
```

### 6.2 Docker Volumes

| Host Path | Container Path | Mode | Purpose |
|-----------|---------------|------|---------|
| `./Keyword` | `/app/data/Keyword` | ro | Product catalog |
| `./testvertex.json` | `/app/data/sa-key.json` | ro | GCP credentials |
| `./work` | `/app/data/work` | rw | State persistence |
| `./logs` | `/app/data/logs` | rw | Log persistence |

---

## 7. Monitoring & Observability

### 7.1 Health Check

**File:** `work/health.json` (updated every poll cycle)

```json
{
  "status": "ok | degraded | error",
  "last_poll": "ISO-8601 timestamp",
  "uptime": "Xh Ym",
  "current_cycle": 42,
  "files_in_queue": 0,
  "last_success": "filename.xlsx (N rows, Xs) @ HH:MM:SS",
  "last_error": null,
  "metrics_summary": {
    "processed_24h": 12,
    "failed_24h": 0,
    "success_rate": "100.0%"
  }
}
```

**Status values:**

| Status | Meaning |
|--------|---------|
| `ok` | Normal operation |
| `degraded` | 3+ consecutive file failures |
| `error` | API unreachable or critical failure |

**Docker health check:** Runs every 60s, verifies `health.json` `last_poll` is within 600s.

### 7.2 Metrics

**File:** `work/metrics.json` (flushed after every poll cycle)

| Metric | Type | Description |
|--------|------|-------------|
| `files_processed` | counter | Total files successfully processed |
| `files_failed` | counter | Total files that failed |
| `total_rows_processed` | counter | Total rows across all files |
| `total_processing_seconds` | gauge | Cumulative processing time |
| `avg_processing_seconds` | derived | Average time per file |
| `success_rate_pct` | derived | Success percentage |
| `gemini_calls` | counter | Total Gemini API calls |
| `gemini_retries` | counter | API retries due to rate limiting |
| `errors_by_type` | map | Error breakdown by exception type |
| `last_success` | object | Last successful file details |
| `last_error` | object | Last error details |

### 7.3 Structured Logs

**File:** `logs/dms-service.jsonl` (10MB × 7 rotation)

```json
{"ts": "2026-05-15T02:34:09", "level": "INFO", "module": "dms-watcher", "msg": "✅ Completed: file.xlsx (135 rows in 67.4s)"}
```

**Fields:**

| Field | Description |
|-------|-------------|
| `ts` | ISO-8601 timestamp |
| `level` | DEBUG / INFO / WARNING / ERROR |
| `module` | Source module name |
| `msg` | Log message |
| `file` | (extra) File being processed |
| `rows` | (extra) Row count |
| `duration` | (extra) Processing duration |
| `error_type` | (extra) Exception class name |

### 7.4 Daily Summary

**File:** `logs/daily-summary.jsonl` (appended daily at midnight)

Contains aggregated daily metrics: files processed, rows, total time, success rate, Gemini usage.

### 7.5 Monitoring Commands

```bash
# Real-time log tailing
docker logs -f dms-feedback-watcher

# Structured log analysis
cat logs/dms-service.jsonl | python3 -m json.tool

# Health check
cat work/health.json | python3 -m json.tool

# Metrics snapshot
cat work/metrics.json | python3 -m json.tool

# Container status
docker inspect --format='{{.State.Health.Status}}' dms-feedback-watcher

# Find errors in logs
grep '"level": "ERROR"' logs/dms-service.jsonl | python3 -m json.tool
```

---

## 8. Troubleshooting

### 8.1 Common Issues

#### Container keeps restarting

```bash
# Check logs for startup errors
docker logs dms-feedback-watcher --tail 50

# Common causes:
# - Missing .env file
# - Invalid credentials
# - Missing Keyword/ files
# - Missing testvertex.json
```

#### Files not being picked up

```bash
# Check seen_files.json for the file status
python3 -c "
import json
with open('work/seen_files.json') as f:
    seen = json.load(f)
for k,v in seen.items():
    if v['status'] != 'done':
        print(v['name'], '→', v['status'])
"
```

**To re-process a file:** Edit `work/seen_files.json` and set the file's `status` to `"retry"`.

#### Authentication failures

```bash
# Azure AD
# - Verify AZURE_TENANT_ID, CLIENT_ID, CLIENT_SECRET
# - Check admin consent status in Azure Portal
# - Verify app permissions: Files.ReadWrite.All, Mail.Send

# GCP / Vertex AI
# - Verify testvertex.json is valid JSON
# - Check GCP_PROJECT_ID matches the SA key
# - Verify Vertex AI API is enabled in GCP Console
```

#### Gemini API rate limiting

The service includes automatic retry with exponential backoff. If persistent:
- Increase `POLL_INTERVAL_SECONDS`
- Reduce `LLM_BATCH_SIZE`
- Check GCP quota limits

### 8.2 Recovery Procedures

#### Reset a failed file

```python
import json
with open('work/seen_files.json', 'r') as f:
    seen = json.load(f)

# Find and reset the file
for fid, entry in seen.items():
    if entry['name'] == 'TARGET_FILE.xlsx':
        entry['status'] = 'retry'
        break

with open('work/seen_files.json', 'w') as f:
    json.dump(seen, f, indent=2)
```

#### Full state reset

```bash
# WARNING: This will re-process ALL files
docker-compose down
rm work/seen_files.json work/metrics.json work/health.json
rm -rf work/checkpoint/* work/input/* work/output/*
docker-compose up -d
```

#### Recover from corrupted checkpoint

```bash
# Remove the specific checkpoint and reset the file
rm work/checkpoint/FILENAME.json
# Then reset the file status in seen_files.json (see above)
```

---

## 9. Maintenance Procedures

### 9.1 Log Rotation

Automatic — configured as 10MB × 7 files. No manual intervention needed.

To manually clear old logs:
```bash
# Logs rotate automatically, but to force clear:
docker-compose down
rm logs/dms-service.jsonl.*
docker-compose up -d
```

### 9.2 Disk Space Management

Monitor these directories:

| Path | Expected Growth | Action |
|------|----------------|--------|
| `logs/` | ~10MB active + 70MB rotated | Auto-managed |
| `work/input/` | Temporary (cleared after processing) | Auto-cleaned |
| `work/output/` | Accumulates output files | Periodic cleanup |
| `work/checkpoint/` | One JSON per file | Small, no action needed |

### 9.3 Updating Product Catalog

```bash
docker-compose down
cp /path/to/new-catalog.xlsx Keyword/Phân\ Chia\ Nhóm\ Sản\ Phẩm\ V2.xlsx
docker-compose up -d
```

### 9.4 Adding New Recipients

Edit `.env`:
```env
NOTIFICATION_RECIPIENTS=user1@example.com,user2@example.com,new-user@example.com
```

Then restart:
```bash
docker-compose down && docker-compose up -d
```

### 9.5 Changing Polling Interval

Edit `.env`:
```env
POLL_INTERVAL_SECONDS=600  # 10 minutes
```

Then restart the container.

---

## 10. Security Model

### 10.1 Authentication

| Service | Method | Scope |
|---------|--------|-------|
| SharePoint | MSAL Client Credentials | `https://graph.microsoft.com/.default` |
| Gemini | GCP Service Account | Vertex AI API |
| Email | MSAL Client Credentials | `Mail.Send` (Application) |

### 10.2 Credential Storage

| Credential | Storage | Protection |
|------------|---------|------------|
| Azure secrets | `.env` file | `.gitignored`, file permissions |
| GCP SA key | `testvertex.json` | `.gitignored`, mounted read-only |
| MSAL tokens | In-memory cache | Not persisted to disk |

### 10.3 Network Requirements

| Destination | Port | Protocol | Purpose |
|-------------|------|----------|---------|
| `login.microsoftonline.com` | 443 | HTTPS | Azure AD auth |
| `graph.microsoft.com` | 443 | HTTPS | SharePoint + Mail |
| `aiplatform.googleapis.com` | 443 | HTTPS | Gemini API |
| `oauth2.googleapis.com` | 443 | HTTPS | GCP auth |

### 10.4 Docker Security

- Base image: `python:3.11-slim` (minimal attack surface)
- Credential files mounted as read-only (`:ro`)
- Reference data mounted as read-only
- Container runs as root (default) — consider adding `USER` directive for production hardening
- Docker logging capped at 10MB × 3 files

---

> **Document version:** 1.0
> **Last updated:** 2026-05-15
> **Maintainer:** DMS Engineering Team
