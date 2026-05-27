import pandas as pd
from pathlib import Path

script_dir = Path(__file__).resolve().parents[1]

old_path = script_dir / "Output" / "DMS-13102025_output.xlsx"
new_path = script_dir / "test_output" / "DMS-13102025_output.xlsx"

old_df = pd.read_excel(old_path, header=0)
new_df = pd.read_excel(new_path, header=0)

output_lines = []
output_lines.append("Old columns:")
output_lines.append(str([str(c) for c in old_df.columns]))

output_lines.append("\nNew columns:")
output_lines.append(str([str(c) for c in new_df.columns]))

output_lines.append("\n--- Row 2 in new_df (dòng 5 Excel) ---")
row_data = new_df.iloc[2] # index 2 is row 5 in Excel
for col in new_df.columns:
    val = row_data[col]
    if pd.notna(val) and str(val).strip() != "":
        output_lines.append(f"  Column '{col}': {val!r}")

output_text = "\n".join(output_lines)
output_file = script_dir / "scratch" / "inspect_excel_labels_results.txt"
output_file.write_text(output_text, encoding="utf-8")
print(f"Inspection complete. Saved to: {output_file}")
