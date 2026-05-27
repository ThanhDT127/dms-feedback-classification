import pandas as pd
from pathlib import Path

script_dir = Path(__file__).resolve().parents[1]
df = pd.read_excel(script_dir / "Output" / "DMS-13102025_output.xlsx", header=0)
print("Columns in header=0:")
print(df.columns.tolist())
