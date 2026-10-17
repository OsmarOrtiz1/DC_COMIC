#======================================================================
#Add manually verified publication years to the DC network.
#Reads the network database and a CSV of manually checked publication
#years. Matches the corrections to comics with missing dates, updates
#a copy of the database and regenerates the network CSVs.
#Unmatched records are reported for further manual correction.
#Existing publication years are preserved.
#========================================================================

import csv
import re
import sqlite3
import unicodedata
from collections import defaultdict
from pathlib import Path


#============================================================
# SETTING
#============================================================

PROJECT_ROOT = Path(
    r"C:\Users\26472724\OneDrive - University of Limerick"
    r"\Desktop\PhD UL\DC_NETWORK"
)

INPUT_FOLDER = (
    PROJECT_ROOT
    / "network_dated_original_identity_test"
)

INPUT_DATABASE = (
    INPUT_FOLDER
    / "network_build.sqlite"
)

MANUAL_YEARS = (
    PROJECT_ROOT
    / "Cleaning and extra steps"
    / "year_manual.csv"
)

OUTPUT_FOLDER = (
    PROJECT_ROOT
    / "network_dated_original_identity_years_added"
)

OUTPUT_DATABASE = (
    OUTPUT_FOLDER
    / "network_build.sqlite"
)

DATE_SOURCE = "manual_publication_year"


#============================================================
# HELPERS
#============================================================

def normalise(value):
    value = unicodedata.normalize(
        "NFC",
        str(value or "")
    )
    return " ".join(value.split()).casefold()


def filename_of(source):
    return Path(
        str(source).replace("\\", "/")
    ).name


def export_csv(db, path, query):
    cursor = db.execute(query)

    with path.open(
        "w",
        encoding="utf-8",
        newline=""
    ) as file:

        writer = csv.writer(file)

        writer.writerow([
            column[0]
            for column in cursor.description
        ])

        count = 0

        while True:
            rows = cursor.fetchmany(10000)

            if not rows:
                break

            writer.writerows(rows)
            count += len(rows)

    print(f"  {path.name}: {count:,} rows")


#============================================================
# LOAD MANUAL YEARS
#============================================================

def load_manual_years():

    corrections = []

    with MANUAL_YEARS.open(
        "r",
        encoding="utf-8-sig",
        newline=""
    ) as file:

        reader = csv.reader(file)

        header = next(reader, None)

        if not header or len(header) != 2:
            raise ValueError(
                "Manual CSV must have exactly two columns."
            )

        if header[1].strip() != "publication_date":
            raise ValueError(
                "Second column must be publication_date."
            )

        for line_number, row in enumerate(reader, 2):

            if not row or not any(row):
                continue

            if len(row) != 2:
                raise ValueError(
                    f"Invalid number of columns on line "
                    f"{line_number}"
                )

            identifier = row[0].strip()
            year_text = row[1].strip()

            if not identifier:
                raise ValueError(
                    f"Empty identifier on line {line_number}"
                )

            if not re.fullmatch(
                r"\d{4}",
                year_text
            ):
                raise ValueError(
                    f"Invalid year on line {line_number}: "
                    f"{year_text}"
                )

            corrections.append(
                (identifier, int(year_text))
            )

    return corrections


#============================================================
# MATCH MANUAL YEARS
#============================================================

def match_corrections(db, corrections):

    #Match manual records to comics using exact filenames.
    #First try the full source path. Otherwise, match a complete
    #filename at the end of the manual identifier, with a whitespace
    #boundary. Ambiguous matches are left unresolved.
    #I struggled with this part so I tried this and seems to match 
    #more of the comics

    comics = db.execute("""
        SELECT source_file, network_year
        FROM comic_dates
    """).fetchall()

    by_source = defaultdict(list)
    by_filename = defaultdict(list)

    for source, current_year in comics:

        source_key = normalise(
            source.replace("\\", "/")
        )

        filename_key = normalise(
            filename_of(source)
        )

        by_source[source_key].append(
            (source, current_year)
        )

        by_filename[filename_key].append(
            (source, current_year)
        )

    filenames = sorted(
        by_filename.keys(),
        key=len,
        reverse=True
    )

    matches = {}
    matched_audit = []
    unresolved = []
    conflicts = []

    for identifier, year in corrections:

        cleaned = identifier.strip()

        # Remove trailing characters from the manual
        # CSV's HTML-derived identifiers.
        cleaned = cleaned.rstrip(" >|;").strip()

        identifier_key = normalise(
            cleaned.replace("\\", "/")
        )

        # Exact source-file match.
        candidates = by_source.get(
            identifier_key,
            []
        )

        # Complete filename at the end of identifier.
        if not candidates:

            for filename in filenames:

                if not identifier_key.endswith(filename):
                    continue

                start = (
                    len(identifier_key)
                    - len(filename)
                )

                if (
                    start > 0
                    and not identifier_key[
                        start - 1
                    ].isspace()
                ):
                    continue

                candidates = by_filename[filename]
                break

        if not candidates:

            unresolved.append({
                "identifier": identifier,
                "manual_year": year,
                "reason": "no_exact_filename_match"
            })

            continue

        if len(candidates) != 1:

            unresolved.append({
                "identifier": identifier,
                "manual_year": year,
                "reason": "ambiguous_filename"
            })

            continue

        source, current_year = candidates[0]

        if current_year is not None:

            if int(current_year) != year:

                conflicts.append({
                    "identifier": identifier,
                    "source_file": source,
                    "existing_year": current_year,
                    "manual_year": year
                })

            else:

                matched_audit.append({
                    "identifier": identifier,
                    "source_file": source,
                    "manual_year": year,
                    "status": "already_dated"
                })

            continue

        if source in matches:

            if matches[source] != year:

                conflicts.append({
                    "identifier": identifier,
                    "source_file": source,
                    "existing_year": matches[source],
                    "manual_year": year
                })

            continue

        matches[source] = year

        matched_audit.append({
            "identifier": identifier,
            "source_file": source,
            "manual_year": year,
            "status": "year_added"
        })

    return (
        matches,
        matched_audit,
        unresolved,
        conflicts
    )


#============================================================
#FIND POSSIBLE MATCHES FOR UNRESOLVED RECORDS
#============================================================

def find_candidates(db, identifier):

   #Suggest possible filename matches without assigning any years

    filenames = [
        row[0]
        for row in db.execute("""
            SELECT source_file
            FROM comic_dates
        """)
    ]

    key = normalise(identifier)

    # Use filename-related keywords from the
    # unresolved identifier.
    terms = [
        word
        for word in re.findall(
            r"[\w]+",
            key,
            flags=re.UNICODE
        )
        if len(word) >= 4
        and word not in {
            "html", "with", "from", "that",
            "this", "edition", "volume"
        }
    ]

    scores = []

    for filename in filenames:

        candidate_key = normalise(filename)

        score = sum(
            term in candidate_key
            for term in set(terms)
        )

        if score >= 2:
            scores.append(
                (score, filename)
            )

    scores.sort(
        key=lambda item: (
            -item[0],
            item[1]
        )
    )

    return [
        filename
        for _, filename in scores[:10]
    ]


#============================================================
#SAVE AUDIT FILES
#===========================================================

def save_audits(
    db,
    matched_audit,
    unresolved,
    conflicts
):

    audit_path = (
        OUTPUT_FOLDER
        / "manual_year_audit.csv"
    )

    with audit_path.open(
        "w",
        encoding="utf-8",
        newline=""
    ) as file:

        writer = csv.writer(file)

        writer.writerow([
            "manual_identifier",
            "source_file",
            "manual_year",
            "status"
        ])

        for item in matched_audit:

            writer.writerow([
                item["identifier"],
                item["source_file"],
                item["manual_year"],
                item["status"]
            ])

    unresolved_path = (
        OUTPUT_FOLDER
        / "unmatched_years.csv"
    )

    with unresolved_path.open(
        "w",
        encoding="utf-8",
        newline=""
    ) as file:

        writer = csv.writer(file)

        writer.writerow([
            "manual_identifier",
            "manual_year",
            "reason",
            "possible_database_filenames"
        ])

        for item in unresolved:

            candidates = find_candidates(
                db,
                item["identifier"]
            )

            writer.writerow([
                item["identifier"],
                item["manual_year"],
                item["reason"],
                " | ".join(candidates)
            ])

    print(
        f"  manual_year_audit.csv: "
        f"{len(matched_audit):,} records"
    )

    print(
        f"  unmatched_years.csv: "
        f"{len(unresolved):,} records"
    )


#============================================================
# MAIN
#============================================================

def main():

    print("=" * 65)
    print("ADDING MANUAL PUBLICATION YEARS")
    print("=" * 65)

    
    # Validate inputs
    

    if not INPUT_DATABASE.is_file():
        raise FileNotFoundError(
            f"Database not found:\n{INPUT_DATABASE}"
        )

    if not MANUAL_YEARS.is_file():
        raise FileNotFoundError(
            f"Manual years not found:\n{MANUAL_YEARS}"
        )

    if OUTPUT_FOLDER.exists():
        raise RuntimeError(
            "The output folder already exists:\n"
            f"{OUTPUT_FOLDER}\n\n"
            "Choose a different OUTPUT_FOLDER to "
            "avoid overwriting previous results."
        )

    
    # Load manual years
    

    print("\n Reading manual years")

    corrections = load_manual_years()

    print(
        f"Manual records: {len(corrections):,}"
    )

    
    # Open source database
    

    print("\nOpening original network")

    source_db = sqlite3.connect(
        INPUT_DATABASE.resolve().as_uri()
        + "?mode=ro",
        uri=True
    )

    
    # Match corrections
    

    print("\n Matching publication years")

    (
        matches,
        matched_audit,
        unresolved,
        conflicts
    ) = match_corrections(
        source_db,
        corrections
    )

    already_dated = sum(
        item["status"] == "already_dated"
        for item in matched_audit
    )

    print(
        f"Matched undated comics: {len(matches):,}"
    )

    print(
        f"Already correctly dated: {already_dated:,}"
    )

    print(
        f"Unresolved: {len(unresolved):,}"
    )

    print(
        f"Conflicts: {len(conflicts):,}"
    )

    if conflicts:

        print("\nConflicting records:")

        for item in conflicts:
            print(item)

        source_db.close()

        raise ValueError(
            "Conflicting publication years found. "
            "No database changes were made."
        )

    if not matches:

        source_db.close()

        raise ValueError(
            "No new publication years matched."
        )

    if unresolved:

        print("\nUnresolved records:")

        for item in unresolved:

            print(
                f"  {item['identifier']}"
            )

        print(
            "\nThese records will NOT receive "
            "automatic year corrections."
        )

   
    # Create copy of database
  

    print("\nCopying network database")

    OUTPUT_FOLDER.mkdir(
        parents=True,
        exist_ok=False
    )

    target_db = sqlite3.connect(
        OUTPUT_DATABASE
    )

    source_db.backup(target_db)
    source_db.close()

    print("Database copied successfully.")

    
    # Apply manual corrections
    

    print("\nApplying manual years")

    updates = [
        (
            year,
            DATE_SOURCE,
            str(year),
            "year",
            "manually_verified",
            source
        )
        for source, year in matches.items()
    ]

    with target_db:

        target_db.executemany("""
            UPDATE comic_dates
            SET
                network_year = ?,
                date_source = ?,
                publication_date = ?,
                publication_precision = ?,
                date_status = ?
            WHERE source_file = ?
              AND network_year IS NULL
        """, updates)

        target_db.executemany("""
            UPDATE pair_story
            SET network_year = ?
            WHERE source_file = ?
              AND network_year IS NULL
        """, [
            (year, source)
            for source, year in matches.items()
        ])

    print(
        f"Publication years added: {len(matches):,}"
    )

    
    # Validate consistency
    

    print("\nValidating corrected network")

    integrity = target_db.execute(
        "PRAGMA quick_check"
    ).fetchone()[0]

    if integrity != "ok":
        raise RuntimeError(
            f"SQLite integrity check failed: {integrity}"
        )

    mismatch_count = target_db.execute("""
        SELECT COUNT(*)
        FROM pair_story p
        JOIN comic_dates d
          ON p.source_file = d.source_file
        WHERE p.network_year IS NOT d.network_year
    """).fetchone()[0]

    if mismatch_count:
        raise RuntimeError(
            f"Year mismatches detected: "
            f"{mismatch_count:,}"
        )

    print("SQLite integrity: OK")
    print("Pair-story year consistency: OK")

    
    # Export comic dates
    

    print("\nExporting corrected CSVs")

    export_csv(
        target_db,
        OUTPUT_FOLDER / "comic_dates.csv",
        """
        SELECT *
        FROM comic_dates
        ORDER BY source_file
        """
    )

    
    # Export story appearances
   

    export_csv(
        target_db,
        OUTPUT_FOLDER / "story_appearances.csv",
        """
        SELECT
            a.source_file,
            a.story,
            a.node_id,
            d.network_year,
            d.date_source

        FROM appearances a

        JOIN comic_dates d
          ON a.source_file = d.source_file

        ORDER BY
            a.source_file,
            a.story,
            a.node_id
        """
    )

    
    # Export nodes
   

    export_csv(
        target_db,
        OUTPUT_FOLDER / "nodes.csv",
        """
        SELECT
            n.node_id,
            n.node_label,
            n.entity_kind,
            n.entity_url,

            COUNT(a.node_id)
                AS story_appearance_count,

            COUNT(DISTINCT a.source_file)
                AS comic_appearance_count,

            MIN(d.network_year)
                AS first_observed_year,

            MAX(d.network_year)
                AS last_observed_year

        FROM nodes n

        LEFT JOIN appearances a
          ON n.node_id = a.node_id

        LEFT JOIN comic_dates d
          ON a.source_file = d.source_file

        GROUP BY n.node_id
        ORDER BY n.node_id
        """
    )


    # Export overall edges


    export_csv(
        target_db,
        OUTPUT_FOLDER / "edges.csv",
        """
        SELECT
            source,
            target,

            COUNT(*)
                AS shared_story_count,

            COUNT(DISTINCT source_file)
                AS shared_comic_count,

            MIN(network_year)
                AS first_shared_year,

            MAX(network_year)
                AS last_shared_year

        FROM pair_story

        GROUP BY source, target

        ORDER BY source, target
        """
    )

   
    # Export yearly edges
   
    export_csv(
        target_db,
        OUTPUT_FOLDER / "edges_by_year.csv",
        """
        SELECT
            network_year,
            source,
            target,

            COUNT(*)
                AS shared_story_count,

            COUNT(DISTINCT source_file)
                AS shared_comic_count

        FROM pair_story

        WHERE network_year IS NOT NULL

        GROUP BY
            network_year,
            source,
            target

        ORDER BY
            network_year,
            source,
            target
        """
    )


    #  Save audit


    print("\nSaving audit files")

    save_audits(
        target_db,
        matched_audit,
        unresolved,
        conflicts
    )

#=================================================
#SUMMARY
#==================================================


    print("\n" + "=" * 65)
    print("FINAL NETWORK SUMMARY")
    print("=" * 65)

    queries = {
        "Nodes": """
            SELECT COUNT(*)
            FROM nodes
        """,

        "Unique undirected edges": """
            SELECT COUNT(*)
            FROM (
                SELECT source, target
                FROM pair_story
                GROUP BY source, target
            )
        """,

        "Story-level pair observations": """
            SELECT COUNT(*)
            FROM pair_story
        """,

        "Dated pair observations": """
            SELECT COUNT(*)
            FROM pair_story
            WHERE network_year IS NOT NULL
        """,

        "Undated pair observations": """
            SELECT COUNT(*)
            FROM pair_story
            WHERE network_year IS NULL
        """,

        "Dated comics": """
            SELECT COUNT(*)
            FROM comic_dates
            WHERE network_year IS NOT NULL
        """,

        "Undated comics": """
            SELECT COUNT(*)
            FROM comic_dates
            WHERE network_year IS NULL
        """,
    }

    for label, query in queries.items():

        value = target_db.execute(
            query
        ).fetchone()[0]

        print(
            f"{label}: {value:,}"
        )

    print("\nManual corrections:")
    print(f"  Added: {len(matches):,}")
    print(f"  Already dated: {already_dated:,}")
    print(f"  Unresolved: {len(unresolved):,}")
    print(f"  Conflicts: {len(conflicts):,}")

    target_db.close()

    print("\nOutput folder:")
    print(OUTPUT_FOLDER.resolve())

    print("\nFinished successfully.")


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()
