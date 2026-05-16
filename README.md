# DMS Feedback Classification Service

English documentation. Vietnamese version: [README.vi.md](README.vi.md).

## What This Service Does

The service watches a SharePoint `Input/` folder for Excel feedback files, classifies each row, uploads the enriched workbook to SharePoint `Output/`, uploads processing checkpoints to `Check_Point/`, and sends notifications.

Current classification flow:

```text
SharePoint Input/
  -> Docker watcher
  -> local baseline model from Model/
  -> keyword and product assets from Keyword/
  -> Gemini refinement through Vertex AI or API key
  -> SharePoint Output/ and Check_Point/
```

## Repository Layout

```text
DMS/
  service/
    src/dms/                 application package
    Keyword/                 committed reference assets
    Model/                   committed baseline model artifacts
    work/                    runtime state, ignored by git
    logs/                    runtime logs, ignored by git
    .env.example             template, committed
    .env                     real environment, ignored by git
    testvertex.json          real GCP service account key, ignored by git
    Dockerfile
    docker-compose.yml
  README.md
  README.vi.md
  OPERATIONS.md
  OPERATIONS.vi.md
```

## What Is Committed

Committed to GitHub:

- source code under `service/src/dms/`
- Docker files
- `service/Keyword/`
- `service/Model/`
- `.env.example`
- documentation

Not committed:

- `service/.env`
- `service/testvertex.json`
- `service/work/`
- `service/logs/`

`work/` is runtime state. It is not source code and should not be shared through git.

## Required Runtime Inputs

After cloning on a new machine, you must provide:

- `service/.env`
- `service/testvertex.json` if using Vertex AI

The repo already includes:

- `service/Keyword/`
- `service/Model/`

## Gemini Backend Options

The service supports two Gemini backends.

### Option 1: Vertex AI

Use this for production.

Required `.env` values:

```env
GEMINI_BACKEND=vertex
GEMINI_MODEL=gemini-2.5-flash-lite
GCP_PROJECT_ID=your-gcp-project-id
GCP_LOCATION=global
GCP_SERVICE_ACCOUNT_JSON=/app/data/sa-key.json
```

Also place the real service account key at:

```text
service/testvertex.json
```

Docker mounts it as:

```text
/app/data/sa-key.json
```

### Option 2: Gemini API key

Use this only if you intentionally run without Vertex AI.

Required `.env` values:

```env
GEMINI_BACKEND=apikey
GEMINI_MODEL=gemini-2.5-flash-lite
GEMINI_API_KEY=your-api-key
```

When `GEMINI_BACKEND=apikey`, `GCP_PROJECT_ID` and `testvertex.json` are not used by the Gemini client.

## SharePoint Requirements

Required `.env` values:

```env
AZURE_TENANT_ID=...
AZURE_CLIENT_ID=...
AZURE_CLIENT_SECRET=...
SHAREPOINT_DRIVE_ID=...
SHAREPOINT_ROOT_FOLDER_ID=...
```

The Azure app registration needs application permissions with admin consent:

- `Files.ReadWrite.All`
- `Mail.Send` if email notifications are used

The configured SharePoint root folder should contain:

```text
Input/
Output/
Check_Point/
Keyword/
Model/
```

`Keyword/` and `Model/` on SharePoint are used when SharePoint config sync is enabled.

## Runtime State Files

The service creates these files automatically in `service/work/`.

| File | Created by | Purpose |
|------|------------|---------|
| `seen_files.json` | watcher | remembers SharePoint file IDs already processed |
| `metrics.json` | metrics collector | stores counters, success rate, timing |
| `health.json` | watcher | stores current service health |
| `config_assets_state.json` | config asset sync | remembers remote asset metadata |
| `config_assets/active/` | config asset sync | last known good `Keyword/` and `Model/` snapshot |

You do not manually create these files for a new empty runtime. The service creates them when it starts.

If you move production to a new VM and do not want old SharePoint files to be processed again, copy `service/work/` from the old machine before starting the new container.

## Quick Start On A New VM

```powershell
git clone https://github.com/ThanhDT127/dms-feedback-classification.git
cd dms-feedback-classification\service
copy .env.example .env
```

Then edit `.env` and place `testvertex.json` if using Vertex AI.

Start the service:

```powershell
docker compose up -d
docker compose ps
docker compose logs -f
```

Check runtime status:

```powershell
Get-Content .\work\health.json
Get-Content .\work\metrics.json
```

## Moving Without Reprocessing Old Files

On the old machine:

```powershell
cd D:\Works\DMS\service
```

Copy these to the new VM:

```text
.env
testvertex.json
work/
```

On the new VM, place them under:

```text
dms-feedback-classification/service/
```

Then run:

```powershell
docker compose up -d
```

The critical file is:

```text
work/seen_files.json
```

Without it, the new VM does not know which SharePoint files were already processed.

## Runtime Cleanup

After a file is successfully processed, uploaded, and marked `done`, the service removes local temporary files:

- `work/input/<file>.xlsx`
- `work/output/<file>_output.xlsx`
- `work/checkpoint/<file>.json`

Protected state is preserved:

- `work/seen_files.json`
- `work/metrics.json`
- `work/health.json`
- `work/config_assets_state.json`
- `work/config_assets/active/`

## Detailed Operations

See [OPERATIONS.md](OPERATIONS.md) for deployment, configuration, state migration, troubleshooting, and maintenance procedures.
