# DMS Service

This directory contains the runnable Docker service.

Main documentation:

- English overview: [../README.md](../README.md)
- Vietnamese overview: [../README.vi.md](../README.vi.md)
- English operations guide: [../OPERATIONS.md](../OPERATIONS.md)
- Vietnamese operations guide: [../OPERATIONS.vi.md](../OPERATIONS.vi.md)

## Quick Start

```powershell
copy .env.example .env
docker compose up -d
docker compose logs -f
```

Before starting on a real machine, provide:

- `.env`
- `testvertex.json` when using `GEMINI_BACKEND=vertex`

If this machine should continue from an existing runtime, copy the old `work/` directory before starting Docker.
