
import csv
import sqlite3
from pathlib import Path

#======================================
#SETTINGS
#======================================

PROJECT_ROOT = Path(
    r"C:\Users\26472724\OneDrive - University of Limerick"
    r"\Desktop\PhD UL\DC_NETWORK"
)


INPUT_FOLDER = (
    PROJECT_ROOT
    / "network_dated_original_identity_years_added"
)

EXTRA_YEARS = PROJECT_ROOT / "extra_years.csv"

OUTPUT_FOLDER = (
    PROJECT_ROOT
    / "network_dated_final"
)

#============================================
#HELPERS
#============================================

def export_csv(db, filename, query):
    cursor = db.execute(query)

    with (OUTPUT_FOLDER / filename).open(
        "w", newline="", encoding="utf-8"
    ) as file:
        writer = csv.writer(file)
        writer.writerow(
            [col[0] for col in cursor.description]
        )

        count = 0
        while True:
            rows = cursor.fetchmany(10000)
            if not rows:
                break
            writer.writerows(rows)
            count += len(rows)

    print(f"  {filename}: {count:,} rows")


def main():
    input_db = INPUT_FOLDER / "network_build.sqlite"

    if not input_db.is_file():
        raise FileNotFoundError(input_db)

    if not EXTRA_YEARS.is_file():
        raise FileNotFoundError(EXTRA_YEARS)

    if OUTPUT_FOLDER.exists():
        raise RuntimeError(
            f"Output folder already exists: {OUTPUT_FOLDER}"
        )

    with EXTRA_YEARS.open(
        newline="", encoding="utf-8-sig"
    ) as file:
        reader = csv.DictReader(file)

        required = {"source_file", "publication_date"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError(
                f"CSV needs columns: {sorted(required)}"
            )

        corrections = {}

        for row in reader:
            source = row["source_file"].strip()
            year_text = row["publication_date"].strip()

            if not source or not (
                len(year_text) == 4
                and year_text.isdigit()
            ):
                raise ValueError(
                    f"Invalid source/year: {row}"
                )

            year = int(year_text)

            if source in corrections:
                raise ValueError(
                    f"Duplicate filename: {source}"
                )

            corrections[source] = year

    print(f"Manual year records: {len(corrections)}")

    source_db = sqlite3.connect(
        input_db.resolve().as_uri() + "?mode=ro",
        uri=True
    )

    existing = dict(source_db.execute(
        "SELECT source_file, network_year FROM comic_dates"
    ))

    # Validate all corrections BEFORE copying/updating.
    problems = []
    updates = []

    for source, year in corrections.items():
        if source not in existing:
            problems.append(
                f"NOT FOUND: {source}"
            )
        elif existing[source] is not None:
            problems.append(
                f"ALREADY DATED: {source} "
                f"(existing={existing[source]}, "
                f"manual={year})"
            )
        else:
            updates.append((source, year))

    if problems:
        print("\nRecords requiring review:")
        for problem in problems:
            print(" ", problem)

        source_db.close()
        raise RuntimeError(
            "No changes made. Resolve the records above."
        )

    print(f"Validated corrections: {len(updates)}")

    OUTPUT_FOLDER.mkdir(parents=True)

    target_db = sqlite3.connect(
        OUTPUT_FOLDER / "network_build.sqlite"
    )

    source_db.backup(target_db)
    source_db.close()

    print("\nApplying publication years...")

    with target_db:
        for source, year in updates:
            target_db.execute(
                """
                UPDATE comic_dates
                SET network_year = ?,
                    date_source = 'manual_publication_year',
                    publication_date = ?,
                    publication_precision = 'year',
                    date_status = 'manually_verified'
                WHERE source_file = ?
                  AND network_year IS NULL
                """,
                (year, str(year), source)
            )

            target_db.execute(
                """
                UPDATE pair_story
                SET network_year = ?
                WHERE source_file = ?
                  AND network_year IS NULL
                """,
                (year, source)
            )

    mismatch = target_db.execute(
        """
        SELECT COUNT(*)
        FROM pair_story p
        JOIN comic_dates d
          ON p.source_file = d.source_file
        WHERE p.network_year IS NOT d.network_year
        """
    ).fetchone()[0]

    if mismatch:
        raise RuntimeError(
            f"Year inconsistencies found: {mismatch}"
        )

    print("\nExporting corrected network...")

    export_csv(
        target_db,
        "comic_dates.csv",
        """
        SELECT *
        FROM comic_dates
        ORDER BY source_file
        """
    )

    export_csv(
        target_db,
        "story_appearances.csv",
        """
        SELECT a.source_file, a.story, a.node_id,
               d.network_year, d.date_source
        FROM appearances a
        JOIN comic_dates d
          ON a.source_file = d.source_file
        ORDER BY a.source_file, a.story, a.node_id
        """
    )

    export_csv(
        target_db,
        "nodes.csv",
        """
        SELECT n.node_id, n.node_label,
               n.entity_kind, n.entity_url,
               COUNT(a.node_id) AS story_appearance_count,
               COUNT(DISTINCT a.source_file)
                   AS comic_appearance_count,
               MIN(d.network_year) AS first_observed_year,
               MAX(d.network_year) AS last_observed_year
        FROM nodes n
        LEFT JOIN appearances a
          ON n.node_id = a.node_id
        LEFT JOIN comic_dates d
          ON a.source_file = d.source_file
        GROUP BY n.node_id
        ORDER BY n.node_id
        """
    )

    export_csv(
        target_db,
        "edges.csv",
        """
        SELECT source, target,
               COUNT(*) AS shared_story_count,
               COUNT(DISTINCT source_file)
                   AS shared_comic_count,
               MIN(network_year) AS first_shared_year,
               MAX(network_year) AS last_shared_year
        FROM pair_story
        GROUP BY source, target
        ORDER BY source, target
        """
    )

    export_csv(
        target_db,
        "edges_by_year.csv",
        """
        SELECT network_year, source, target,
               COUNT(*) AS shared_story_count,
               COUNT(DISTINCT source_file)
                   AS shared_comic_count
        FROM pair_story
        WHERE network_year IS NOT NULL
        GROUP BY network_year, source, target
        ORDER BY network_year, source, target
        """
    )

    with (OUTPUT_FOLDER / "extra_year_audit.csv").open(
        "w", newline="", encoding="utf-8"
    ) as file:
        writer = csv.writer(file)
        writer.writerow(["source_file", "year_added"])
        writer.writerows(updates)

    dated, undated = target_db.execute(
        """
        SELECT
            SUM(network_year IS NOT NULL),
            SUM(network_year IS NULL)
        FROM comic_dates
        """
    ).fetchone()

    print("\nFINAL SUMMARY")
    print(f"Years added: {len(updates)}")
    print(f"Dated comics: {dated:,}")
    print(f"Undated comics: {undated:,}")
    print(f"Year mismatches: {mismatch}")
    print(f"Output: {OUTPUT_FOLDER}")

    target_db.close()


if __name__ == "__main__":
    main()
