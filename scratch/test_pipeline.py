import sys
import os
import time
from pathlib import Path

script_dir = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(script_dir / "service" / "src"))

import pandas as pd
from dms.settings import get_settings
from dms.gemini_client import GeminiClient
from dms.pipeline.rag_product import RAGProductMatcher
from dms.pipeline.runner import PipelineRunner

# Configure logging to stdout so we can see what's happening
import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("dms-watcher")
logger.setLevel(logging.INFO)

from dms.metrics import MetricsCollector

print("Loading settings...")
settings = get_settings()

# Let's override settings to make it super fast for test
settings = settings.model_copy(
    update={
        "llm_batch_size": 2, # Process only 2 rows in one batch
        "rate_gap_sec": 1.0,  # Fast sleep
    }
)

print("Initializing clients...")
gemini = GeminiClient(settings)
rag = RAGProductMatcher(settings, gemini)
metrics = MetricsCollector(settings.metrics_path)

runner = PipelineRunner(
    gemini=gemini,
    rag=rag,
    metrics=metrics,
    settings=settings
)

# Load first 5 rows of test file
input_path = script_dir / "Input" / "DMS-13102025.xlsx"
df = pd.read_excel(input_path)
print("Input file rows:", len(df))

# Create a temporary input file with only 4 rows
temp_input = script_dir / "scratch" / "temp_input.xlsx"
df.head(4).to_excel(temp_input, index=False)

temp_output = script_dir / "scratch" / "temp_output.xlsx"
temp_ckpt = script_dir / "scratch" / "temp_ckpt.json"

if temp_output.exists():
    temp_output.unlink()
if temp_ckpt.exists():
    temp_ckpt.unlink()

print("Running pipeline on 4 rows...")
t0 = time.time()
try:
    results = runner.run_pipeline(
        input_path=temp_input,
        output_path=temp_output,
        ckpt_path=temp_ckpt
    )
    print(f"Pipeline finished successfully in {time.time() - t0:.1f}s!")
    print("Output file exists:", temp_output.exists())
except Exception as e:
    print("Pipeline failed with error:", e)
