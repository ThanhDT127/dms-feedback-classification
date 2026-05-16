# DMS Operations Guide

English documentation. Vietnamese version: [OPERATIONS.vi.md](OPERATIONS.vi.md).

## 1. Service Overview

The DMS service is a Dockerized Python watcher. It runs from `service/` with:

```text
python -m dms
```

Main responsibilities:

- poll SharePoint `Input/`
- download new `.xlsx` files
- classify feedback rows using local baseline model, keyword/product assets, and Gemini
- upload output workbooks to SharePoint `Output/`
- upload checkpoints to SharePoint `Check_Point/`
- write local health, metrics, and state files
- clean temporary local artifacts after confirmed success

## 2. Runtime Directories

```text
service/
  src/dms/                 source code
  Keyword/                 source keyword/product assets, committed
  Model/                   source baseline model artifacts, committed
  work/                    runtime state, ignored by git
  logs/                    runtime logs, ignored by git
  .env                     local secrets and config, ignored by git
  testvertex.json          GCP service account key, ignored by git
```

Docker mounts:

| Host path | Container path | Mode | Purpose |
|-----------|----------------|------|---------|
| `./Keyword` | `/app/data/Keyword` | read-only | fallback keyword/product assets |
| `./Model` | `/app/data/Model` | read-only | fallback model artifacts |
| `./testvertex.json` | `/app/data/sa-key.json` | read-only | Vertex AI service account key |
| `./work` | `/app/data/work` | read-write | runtime state |
| `./logs` | `/app/data/logs` | read-write | service logs |

## 3. Required Configuration

All runtime configuration comes from `service/.env` plus fixed values in `docker-compose.yml`.

Create `.env` from the template:

```powershell
cd dms-feedback-classification\service
copy .env.example .env
```

### Azure AD

Required:

```env
AZURE_TENANT_ID=...
AZURE_CLIENT_ID=...
AZURE_CLIENT_SECRET=...
```

Azure app registration permissions:

- `Files.ReadWrite.All` for SharePoint file access
- `Mail.Send` if email notifications are used

Both must be application permissions with admin consent.

### SharePoint

Required:

```env
SHAREPOINT_DRIVE_ID=...
SHAREPOINT_ROOT_FOLDER_ID=...
```

Expected folder structure under `SHAREPOINT_ROOT_FOLDER_ID`:

```text
Input/
Output/
Check_Point/
Keyword/
Model/
```

`Input/`, `Output/`, and `Check_Point/` are used for file processing.

`Keyword/` and `Model/` are used for SharePoint config sync when enabled.

### Gemini backend: Vertex AI

Recommended for production.

`.env`:

```env
GEMINI_BACKEND=vertex
GEMINI_MODEL=gemini-2.5-flash-lite
GCP_PROJECT_ID=your-project-id
GCP_LOCATION=global
GCP_SERVICE_ACCOUNT_JSON=/app/data/sa-key.json
```

Required local file:

```text
service/testvertex.json
```

The GCP project must have Vertex AI API enabled, and the service account must have permission to call the selected Gemini model.

### Gemini backend: API key

Optional mode.

`.env`:

```env
GEMINI_BACKEND=apikey
GEMINI_MODEL=gemini-2.5-flash-lite
GEMINI_API_KEY=your-api-key
```

In this mode, the Gemini client does not use `testvertex.json`.

### Processing settings

```env
POLL_INTERVAL_SECONDS=300
LLM_BATCH_SIZE=20
CKPT_EVERY=50
```

Meaning:

- `POLL_INTERVAL_SECONDS`: how often the watcher checks SharePoint
- `LLM_BATCH_SIZE`: number of Excel rows per Gemini batch
- `CKPT_EVERY`: checkpoint frequency during pipeline processing

### SharePoint config sync

```env
ENABLE_SHAREPOINT_CONFIG_SYNC=true
SHAREPOINT_KEYWORD_FOLDER=Keyword
SHAREPOINT_MODEL_FOLDER=Model
```

When enabled:

- the service checks SharePoint `Keyword/` and `Model/` before each poll cycle
- changed tracked assets are downloaded into staging folders under `work/config_assets/`
- validated assets are published into `work/config_assets/active/`
- runtime dependencies are reloaded between cycles

### Runtime cleanup

```env
ENABLE_RUNTIME_CLEANUP=true
CLEANUP_OUTPUT_TTL_DAYS=7
CLEANUP_LOG_TTL_DAYS=7
CLEANUP_STAGING_TTL_HOURS=24
```

Cleanup removes temporary files but preserves state needed for continuity.

## 4. Runtime State Files

The service creates these automatically.

| Path | Created when | Purpose |
|------|--------------|---------|
| `work/seen_files.json` | first watcher save | remembers processed SharePoint item IDs |
| `work/metrics.json` | first metrics flush | counters, durations, success rate |
| `work/health.json` | first health update | current service status |
| `work/config_assets_state.json` | first config sync | remote `Keyword/` and `Model/` metadata |
| `work/config_assets/active/` | first successful config sync | last known good runtime asset snapshot |

You do not create these JSON files manually for a fresh runtime. Start the service and it will create them.

Important distinction:

- `Keyword/` and `Model/` are committed fallback source assets
- `work/config_assets/active/` is the live synced snapshot when SharePoint config sync is active

## 5. Fresh VM Deployment

Use this when a new VM is allowed to start as a new runtime instance.

```powershell
git clone https://github.com/ThanhDT127/dms-feedback-classification.git
cd dms-feedback-classification\service
copy .env.example .env
```

Then:

1. Fill `.env`
2. Place `testvertex.json` if using Vertex AI
3. Confirm `Keyword/` exists
4. Confirm `Model/` exists
5. Start Docker

```powershell
docker compose up -d
docker compose ps
docker compose logs -f
```

Risk:

- if `work/seen_files.json` is absent, the VM does not know which SharePoint files were processed earlier
- old files in SharePoint `Input/` may be processed again

## 6. VM Migration Without Reprocessing

Use this when replacing or moving the current running service.

From the old machine, copy:

```text
service/.env
service/testvertex.json
service/work/
```

Place them into the cloned repo on the new VM:

```text
dms-feedback-classification/service/.env
dms-feedback-classification/service/testvertex.json
dms-feedback-classification/service/work/
```

Then start:

```powershell
cd dms-feedback-classification\service
docker compose up -d
docker compose logs -f
```

Minimum state to preserve:

- `work/seen_files.json`
- `work/config_assets_state.json`
- `work/config_assets/active/`

Best practice:

- copy the whole `service/work/` directory

Why:

- `seen_files.json` prevents old SharePoint files from being processed again
- `config_assets_state.json` preserves remote asset metadata
- `config_assets/active/` preserves the last known good synced asset snapshot

## 7. Checkpoint And Resume

Per-file checkpoints live under:

```text
work/checkpoint/
```

They are useful while a file is actively processing or retrying.

After a file has successfully completed, uploaded, and been marked `done`, local temporary artifacts are cleaned:

- `work/input/<file>.xlsx`
- `work/output/<file>_output.xlsx`
- `work/checkpoint/<file>.json`

So for completed files, resume is primarily controlled by:

```text
work/seen_files.json
```

To force one file to run again:

1. Stop the container
2. Open `work/seen_files.json`
3. Find the entry by `name`
4. Remove the entry or change `status` to `retry`
5. Start the container again

Example PowerShell status check:

```powershell
Get-Content .\work\seen_files.json
```

## 8. Config Asset Updates From SharePoint

When a keyword or model file is updated on SharePoint:

1. watcher detects remote metadata change at the next poll cycle
2. changed file is downloaded to `work/config_assets/cfgsync-*`
3. assets are validated
4. active snapshot is updated under `work/config_assets/active/`
5. pipeline dependencies are reloaded before processing files

The local source files under `service/Keyword/` and `service/Model/` are not overwritten.

To inspect active synced keyword data:

```powershell
Get-Content .\work\config_assets\active\Keyword\kw_map.json
```

## 9. Monitoring

Useful commands from `service/`:

```powershell
docker compose ps
docker compose logs -f
Get-Content .\work\health.json
Get-Content .\work\metrics.json
Get-Content .\work\config_assets_state.json
```

Expected healthy signs:

- container is `Up`
- logs show `Composition root ready`
- logs show poll cycles
- `health.json` updates `last_poll`
- SharePoint `Input/` is listed successfully

## 10. Troubleshooting

### Container does not start

Check:

- `.env` exists
- `testvertex.json` exists when using Vertex AI
- `Keyword/` exists
- `Model/` exists
- Azure client secret is valid
- SharePoint IDs are correct

### `No such container: dms-feedback-watcher`

The service is not running.

```powershell
cd dms-feedback-classification\service
docker compose up -d
docker compose ps
```

### SharePoint asset changed but `service/Keyword/` did not change

This is expected.

Runtime reads the synced snapshot from:

```text
work/config_assets/active/
```

The source `Keyword/` and `Model/` directories are read-only fallbacks.

### New VM processes old files

Cause:

- `work/seen_files.json` was not copied from the old machine

Fix:

- stop the VM container
- copy the old `service/work/` directory
- start the container again

## 11. Security Notes

Never commit:

- `.env`
- `testvertex.json`
- Azure client secrets
- API keys
- `work/`
- `logs/`

If a secret was exposed, rotate it in Azure or GCP before relying on the environment.
