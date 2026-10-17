import pandas as pd

# Load dataset
df = pd.read_csv(r"C:\Users\26472724\OneDrive - University of Limerick\Desktop\PhD UL\DC_NETWORK\data\comic_characters_cleaned.backup_20260930_022849_611185.csv")

# ============================================================
# How many unique comics, stories and characters are there?
# ============================================================
print("\n" + "=" * 60)
print("UNIQUENESS")
print("=" * 60)

print(f"Unique comics: {df['comic'].nunique():,}")

print(f"Unique stories: {df['story'].nunique():,}")

print(f"Unique character names: {df['entity_name'].nunique():,}")

print(f"Unique URLs: {df['entity_url'].nunique(dropna=True):,}")

# ============================================================
# Character types
# ============================================================
print("\n" + "=" * 60)
print("CHARACTER TYPES")
print("=" * 60)

print(df["listed_as"].value_counts())

# ============================================================
# Association matches
# ============================================================
print("\n" + "=" * 60)
print("ASSOCIATION MATCHES")
print("=" * 60)

print(df["entity_kind"].value_counts())

# ============================================================
# Percentages
# ============================================================
print("\n" + "=" * 60)
print("PERCENTAGES")
print("=" * 60)

print(
    (
        df["entity_kind"]
        .value_counts(normalize=True)
        * 100
    ).round(2)
)
# ============================================================
# UNIQUE CHARACTERS BY ASSOCIATION STATUS
# ============================================================

print("\n" + "=" * 60)
print("UNIQUE CHARACTERS")
print("=" * 60)

# Unique character names with any type of association match
matched_characters = (
    df[df["entity_kind"] != "none"]["entity_name"]
    .nunique()
)

# Unique character names with no association match
non_matched_characters = (
    df[df["entity_kind"] == "none"]["entity_name"]
    .nunique()
)

print(f"Unique characters with an association match: {matched_characters:,}")
print(f"Unique characters without an association match: {non_matched_characters:,}")