import pandas as pd
from pathlib import Path

script_dir = Path(__file__).resolve().parents[1]
df = pd.read_excel(script_dir / "Output" / "DMS-13102025_output.xlsx", header=None)

lines = []
lines.append("Shape: " + str(df.shape))
lines.append("\n--- FIRST 10 ROWS ---")
for idx, row in df.head(10).iterrows():
    lines.append(f"Row {idx}: {row.tolist()[:15]}")

Path(script_dir / "scratch" / "excel_structure.txt").write_text("\n".join(lines), encoding="utf-8")
print("Structure saved successfully.")
