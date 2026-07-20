import urllib.request
import json
from pathlib import Path

try:
    with urllib.request.urlopen("http://127.0.0.1:8501/api/classify/jobs") as response:
        html = response.read().decode("utf-8")
        jobs = json.loads(html)
        out_path = Path("scratch/jobs_status.json")
        out_path.write_text(
            json.dumps(jobs, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print("Successfully wrote jobs status to scratch/jobs_status.json")
except Exception as e:
    print("Error querying jobs:", e)
