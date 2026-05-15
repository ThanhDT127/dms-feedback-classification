# 🏭 DMS Feedback Classification Service

**Automated feedback classification pipeline** that monitors a SharePoint folder for Excel files, classifies customer feedback using Google Gemini LLM with RAG (Retrieval-Augmented Generation), and uploads enriched results — all running autonomously as a Docker container.

---

## ✨ Key Features

| Feature | Description |
|---------|-------------|
| 🔄 **Auto-polling** | Monitors SharePoint folder for new `.xlsx` files at configurable intervals |
| 🤖 **LLM Classification** | Uses Gemini (Vertex AI or API Key) to classify feedback into product categories and issue types |
| 📊 **RAG Pipeline** | Matches products using BM25 + fuzzy matching before LLM classification |
| 📝 **Structured Logging** | JSON Lines logs with rotation (10MB × 7 files), persisted via Docker volumes |
| 📈 **Operational Metrics** | Real-time `metrics.json` tracking success rates, Gemini API usage, and processing performance |
| 🏥 **Health Checks** | Docker-native health check with enriched `health.json` diagnostics |
| 📧 **Email Notifications** | Automatic email notifications via Microsoft Graph API on file completion |
| 💾 **Checkpoint/Resume** | Batch-level checkpointing — resumes processing after crash without data loss |
| 🐳 **Docker Ready** | Self-contained deployment with `docker-compose up -d` |

---

## 🏗️ Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                    SharePoint (Microsoft 365)                    │
│                                                                  │
│   📁 Input/              📁 Output/            📁 Check_Point   │
│   ┌─────────┐            ┌──────────────┐      ┌─────────┐       │
│   │ .xlsx   │──download──│ _output.xlsx │      │  .json  │       │
│   └─────────┘            └──────────────┘      └─────────┘       │
└───────┬──────────────────────────▲──────────────────▲────────────┘
        │                         │                  │
  ┌─────▼─────────────────────────┴──────────────────┴─────────┐
  │                    DMS Service (Docker)                    │
  │                                                            │
  │  watcher.py ──► pipeline/runner.py ──► sharepoint.py       │
  │       │              │                       │             │
  │       │         ┌────▼────┐            ┌─────▼─────┐       │
  │       │         │ RAG     │            │ Upload +  │       │
  │       │         │ Product │            │ Notify    │       │
  │       │         │ Matcher │            └───────────┘       │
  │       │         └────┬────┘                                │
  │       │         ┌────▼────────┐                            │
  │       │         │ Gemini LLM  │                            │
  │       │         │ (Issue +    │                            │
  │       │         │  Product)   │                            │
  │       │         └─────────────┘                            │
  │       │                                                    │
  │  ┌────▼─────────────────────────────────────────┐          │
  │  │ Observability Layer                          │          │
  │  │ • metrics.json   • health.json               │          │
  │  │ • logs/*.jsonl   • daily-summary.jsonl       │          │
  │  └──────────────────────────────────────────────┘          │
  └────────────────────────────────────────────────────────────┘
```

---

## 🚀 Quick Start

### Prerequisites

- **Docker** & **Docker Compose**
- **Azure AD App Registration** with:
  - `Files.ReadWrite.All` (Application) — SharePoint access
  - `Mail.Send` (Application) — Email notifications
  - Admin consent granted
- **Google Cloud Service Account** with Vertex AI API enabled

### 1. Clone & Configure

```bash
git clone https://github.com/YOUR_USERNAME/dms-feedback-classification.git
cd dms-feedback-classification/service

# Copy and fill in your credentials
cp .env.example .env
# Edit .env with your Azure AD, GCP, and SharePoint configuration
```

### 2. Prepare Data Files

```bash
# Place your product catalog in Keyword/
cp /path/to/your-product-catalog.xlsx Keyword/

# Place your GCP service account key
cp /path/to/sa-key.json testvertex.json
```

### 3. Deploy

```bash
docker-compose up -d

# Check logs
docker logs -f dms-feedback-watcher

# Verify health
cat work/health.json | python -m json.tool
```

---

## 📁 Project Structure

```
service/
├── watcher.py              # Main polling loop & orchestration
├── config.py               # Centralized configuration from env vars
├── auth.py                 # MSAL authentication (Client Credentials)
├── sharepoint.py           # SharePoint Graph API operations
├── notification.py         # Email notification via Graph API
├── gemini_client.py        # Gemini LLM client (Vertex AI / API Key)
├── logging_config.py       # JSON Lines structured logging setup
├── metrics.py              # Operational metrics collector
├── pipeline/
│   ├── runner.py           # Batch processing orchestrator
│   ├── rag_product.py      # RAG-based product matching (BM25 + fuzzy)
│   ├── issue_classifier.py # LLM-based issue type classification
│   └── excel_formatter.py  # Output Excel formatting
├── scripts/
│   ├── test_email.py       # Email notification test
│   ├── test_pipeline.py    # Pipeline integration test
│   ├── test_sharepoint.py  # SharePoint connectivity test
│   ├── check_users.py      # Azure AD user lookup utility
│   └── setup_deployment.py # Deployment preparation script
├── Keyword/                # Product catalog reference data
├── work/                   # Persistent state (checkpoints, seen files)
├── logs/                   # Persistent JSON logs
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── .env.example
```

---

## ⚙️ Configuration

All configuration is via environment variables. See [`.env.example`](service/.env.example) for the full list.

### Core Settings

| Variable | Required | Description |
|----------|----------|-------------|
| `AZURE_TENANT_ID` | ✅ | Azure AD tenant ID |
| `AZURE_CLIENT_ID` | ✅ | App registration client ID |
| `AZURE_CLIENT_SECRET` | ✅ | App registration client secret |
| `SHAREPOINT_DRIVE_ID` | ✅ | SharePoint document library drive ID |
| `SHAREPOINT_ROOT_FOLDER_ID` | ✅ | Root folder item ID |
| `GEMINI_BACKEND` | ❌ | `vertex` (default) or `apikey` |
| `GEMINI_MODEL` | ❌ | Model name (default: `gemini-2.5-flash-lite`) |
| `GCP_PROJECT_ID` | ✅* | Required when using Vertex AI |
| `POLL_INTERVAL_SECONDS` | ❌ | Polling interval (default: `300`) |

### Notification Settings

| Variable | Required | Description |
|----------|----------|-------------|
| `NOTIFICATION_SENDER_EMAIL` | ❌ | Sender mailbox (must exist in Azure AD) |
| `NOTIFICATION_RECIPIENTS` | ❌ | Comma-separated recipient emails |

---

## 📊 Monitoring

### Health Check

```bash
cat work/health.json
```

```json
{
  "status": "ok",
  "last_poll": "2026-05-15T02:34:09",
  "uptime": "2h 15m",
  "files_in_queue": 0,
  "last_success": "DMST0426-10-10.xlsx (135 rows, 67.4s) @ 02:34:08",
  "metrics_summary": {
    "processed_24h": 12,
    "failed_24h": 0,
    "success_rate": "100.0%"
  }
}
```

### Metrics

```bash
cat work/metrics.json
```

Tracks: files processed/failed, rows, processing time, Gemini API calls/retries, error breakdown.

### Logs

```bash
# Structured JSON Lines
tail -f logs/dms-service.jsonl | python -m json.tool

# Daily summary
cat logs/daily-summary.jsonl
```

---

## 🛡️ Security

- **No hardcoded credentials** — all secrets via environment variables
- **Client Credentials Flow** — no user interaction required
- **SA key mounted read-only** in Docker
- **Reference data mounted read-only**
- `.env` and credential files are `.gitignored`

---

## 📄 License

This project is provided as-is for internal use. See [OPERATIONS.md](OPERATIONS.md) for detailed deployment and operational documentation.

---

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request
