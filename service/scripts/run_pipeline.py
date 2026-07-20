import sys
import os
import time
from pathlib import Path

# Add service/src to python path
script_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(script_dir.parent / "src"))

import pandas as pd
from dms.settings import get_settings
from dms.gemini_client import GeminiClient
from dms.pipeline.rag_product import RAGProductMatcher
from dms.pipeline.runner import PipelineRunner
from dms.metrics import MetricsCollector

def run_file(input_file_name: str):
    print(f"\n==================================================")
    print(f"Starting classification on: {input_file_name}")
    print(f"==================================================")
    
    input_path = script_dir / "Input" / input_file_name
    if not input_path.exists():
        print(f"Error: Input file '{input_path}' does not exist!")
        return
        
    output_path = script_dir / "Output" / input_file_name.replace(".xlsx", "_output.xlsx")
    ckpt_path = script_dir / "service" / "work" / "checkpoint" / input_file_name.replace(".xlsx", ".json")
    
    # 1. Load settings
    print("Loading service settings...")
    settings = get_settings()
    
    # Set keyword and model overrides if synchronized cache exists
    active_keyword = settings.active_keyword_dir
    active_model = settings.active_model_dir
    
    runtime_settings = settings.model_copy(
        update={
            "keyword_dir_override": active_keyword if active_keyword.exists() else settings.keyword_dir,
            "model_dir_override": active_model if active_model.exists() else settings.model_dir,
        }
    )
    
    print(f"Using keyword directory: {runtime_settings.keyword_dir}")
    print(f"Using model directory: {runtime_settings.model_dir}")
    print(f"Gemini Backend: {runtime_settings.gemini_backend} ({runtime_settings.gemini_model})")
    
    # 2. Init components
    print("Initializing components...")
    gemini = GeminiClient(runtime_settings)
    rag = RAGProductMatcher(runtime_settings, gemini)
    metrics = MetricsCollector(runtime_settings.metrics_path)
    
    runner = PipelineRunner(
        gemini=gemini,
        rag=rag,
        metrics=metrics,
        settings=runtime_settings
    )
    
    # 3. Execute
    t0 = time.time()
    try:
        results = runner.run_pipeline(
            input_path=input_path,
            output_path=output_path,
            ckpt_path=ckpt_path
        )
        elapsed = time.time() - t0
        print(f"\n[Success] Classification complete in {elapsed:.1f}s!")
        print(f"Total Rows: {results['total_rows']}")
        print(f"Processed Rows: {results['processed_rows']}")
        print(f"Saved Output Excel: {results['output_path']}")
        
        # Clean checkpoint upon success
        if ckpt_path.exists():
            ckpt_path.unlink()
            print("Checkpoint cleaned up successfully.")
            
    except Exception as e:
        print(f"\n[Error] Pipeline execution failed: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python run_pipeline.py <input_file_name_in_Input_dir>")
        print("Example: python run_pipeline.py DMS-13102025.xlsx")
        
        # List files in Input dir for easy choosing
        input_dir = script_dir / "Input"
        if input_dir.exists():
            print("\nAvailable files in Input directory:")
            for f in sorted(input_dir.glob("*.xlsx")):
                print(f" - {f.name}")
    else:
        run_file(sys.argv[1])
