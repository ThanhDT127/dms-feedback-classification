# Makefile for DMS Feedback Classification Service

.PHONY: setup test run clean format

setup:
	d:\Works\.venv\Scripts\pip install -r service/requirements.txt

test:
	d:\Works\.venv\Scripts\pytest service/tests/

run:
	d:\Works\.venv\Scripts\python run_pipeline.py $(FILE)

format:
	d:\Works\.venv\Scripts\ruff format service/src/ service/tests/
	d:\Works\.venv\Scripts\ruff check --fix service/src/ service/tests/

clean:
	powershell -Command "Get-ChildItem -Path . -Include __pycache__,.pytest_cache,.ruff_cache,.mypy_cache -Recurse -Force | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue"
