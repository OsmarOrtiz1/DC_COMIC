import os
import pandas as pd

HTML_FOLDER = r"C:\Users\26472724\OneDrive - University of Limerick\Desktop\PhD UL\DC_NETWORK\comic_pages"

# Every HTML file 
all_html = {
    f for f in os.listdir(HTML_FOLDER)
    if f.endswith(".html")
}

# Every HTML file already processed
processed = set(
    pd.read_csv(r"C:\Users\26472724\OneDrive - University of Limerick\Desktop\PhD UL\DC_NETWORK\data\comic_characters_cleaned.csv")["source_file"]
)

missing = sorted(all_html - processed)

print(f"HTML files: {len(all_html):,}")
print(f"Processed: {len(processed):,}")
print(f"Missing: {len(missing):,}")

# Save list
pd.DataFrame({"source_file": missing}).to_csv(
    r"C:\Users\26472724\OneDrive - University of Limerick\Desktop\PhD UL\DC_NETWORK\data\missing_html.csv",
    index=False
)