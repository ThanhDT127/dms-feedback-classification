"""Entry point: python -m dms.web"""

from __future__ import annotations

import os

import uvicorn

from ..settings import SERVICE_DIR
from .app import create_app


def main() -> None:
    # Ensure CWD is SERVICE_DIR so relative paths in .env (e.g. ./testvertex.json) resolve correctly
    os.chdir(SERVICE_DIR)
    app = create_app()
    uvicorn.run(app, host="0.0.0.0", port=8501)


if __name__ == "__main__":
    main()
