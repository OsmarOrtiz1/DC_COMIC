#=========================================================================
#Build a dated DC Comics character co-appearance network.
#Reads the manually validated character dataset and extracts comic
#publication dates from locally saved HTML pages.
#Creates node, edge, story appearance and date datasets using SQLite. I was struggling with with space to run this so I decided to use SQL
#==========================================================================

import csv
import re
import sqlite3
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, unquote

from bs4 import BeautifulSoup


# ============================================================
# SETTINGS
# ============================================================

PROJECT_ROOT = Path(
    r"C:\Users\26472724\OneDrive - University of Limerick"
    r"\Desktop\PhD UL\DC_NETWORK"
)

INPUT_FILE = (
    PROJECT_ROOT
    / "data"
    / "scraper_v3"
    / "comic_characters_cleaned.csv"
)

HTML_FOLDER = PROJECT_ROOT / "comic_pages"

REPAIRED_HTML_FOLDER = None

# Separate folder, original results remain untouched in case I want to double check something

OUTPUT_FOLDER = (
    PROJECT_ROOT
    / "network_dated_original_identity_test"
)


#============================================================
# NETWORK SETTINGS
#============================================================

# All four categories have been manually validated.
INCLUDED_ENTITY_KINDS = {
    "character",
    "external_url",
    "manual_review",
    "unknown",
}

APPEARANCE_LABELS = {
    "Featured Characters",
    "Supporting Characters",
    "Antagonists",
    "Other Characters",
    "Characters",
    "Main Characters",
    "Guest Characters",
}

# This setting can be change if I want to generate a new network 
# I will use it to create the network excluding mentioned_only suggested by PM
INCLUDE_MENTIONED_ONLY = True

EXCLUDE_EXACT_DUPLICATES = True

PROTECT_EXISTING_DATABASE = True

BATCH_SIZE = 10000

DATE_WORKERS = 10
DATE_CHUNKSIZE = 16


#============================================================
#DATE SETTINGS
#============================================================

MONTH = (
    r"(?:January|February|March|April|May|June|"
    r"July|August|September|October|November|December)"
)

DATE_PATTERN = (
    rf"({MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?\s*,\s*\d{{4}}"
    rf"|{MONTH}\s*,?\s*\d{{4}}"
    rf"|\d{{4}})"
)


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def tidy(value):
    return " ".join(str(value or "").split())


def yes(value):
    return tidy(value).casefold() in {
        "true", "1", "yes"
    }


def normalize_source(value):
    return tidy(value).replace("\\", "/")


def universe_from_text(text):
    
    #A final parenthetical label is treated as a universe.
    #Examples:
    #    Bruce Wayne (New Earth) = New Earth
    #    Clark Kent (Prime Earth) = Prime Earth
    #    Batman = Unknown
    #I think we can use this in the future to simulate 
    #we miss character from a specific universe

    match = re.search(
        r"\(([^()]+)\)\s*$",
        tidy(text)
    )

    if match:
        return match.group(1).strip()

    return "Unknown"


def node_for(row):

    #Assign a node ID using the charecter identity rules.
    #Use DC URL when they are verified characters
    #otherwise, creates a story-specific ID to avoid 
    #merching potentially different character with the same name.

    url = tidy(row["entity_url"])

    display = tidy(row["entity_display_name"])

    name = tidy(
        row["entity_name"] or display
    )

    parts = urlsplit(url) if url else None

    wiki_path = ""

    if parts is not None:
        if (
            parts.hostname
            and parts.hostname.casefold() == "dc.fandom.com"
            and parts.path.startswith("/wiki/")
        ):
            wiki_path = unquote(parts.path)

    universe = "Unknown"

    if wiki_path:
        universe = universe_from_text(display)

        if universe == "Unknown":
            universe = universe_from_text(
                wiki_path.replace("_", " ")
            )

    if wiki_path and universe != "Unknown":
        return wiki_path

    slug = re.sub(
        r"[^a-z0-9]+",
        "-",
        name.casefold()
    ).strip("-")

    if not slug:
        return None

    return (
        "unknown::"
        + normalize_source(row["source_file"])
        + "::"
        + tidy(row["story"])
        + "::"
        + slug
    )


#============================================================
# DATE EXTRACTION
#============================================================

def parse_date(raw):
    #Parse a date with day,month or year precision it depends whats available 

    raw = re.sub(
        r"(?<=\d)(st|nd|rd|th)\b",
        "",
        raw,
        flags=re.I
    )

    raw = re.sub(
        r"\s*,\s*",
        ", ",
        raw
    ).strip()

    formats = [
        ("%B %d, %Y", "day", "%Y-%m-%d"),
        ("%B, %Y", "month", "%Y-%m"),
        ("%B %Y", "month", "%Y-%m"),
        ("%Y", "year", "%Y"),
    ]

    for fmt, precision, output_fmt in formats:
        try:
            parsed = datetime.strptime(raw, fmt)

            return (
                parsed.strftime(output_fmt),
                precision
            )

        except ValueError:
            continue

    return "", ""


def extract_date(path):
   
    #Date prioritise publication date and use 
    #cover data as falling back if publication data is missing
   
    soup = BeautifulSoup(
        path.read_bytes(),
        "lxml"
    )

    content = soup.select_one(
        ".mw-parser-output"
    )

    if content is None:
        return (
            "", "", "", "", "",
            "missing_article_content"
        )

    parts = []

    for child in content.children:

        name = getattr(child, "name", None)

        if name in {"h2", "h3"}:
            break

        if name is None:
            parts.append(str(child))

        elif name in {
            "p", "a", "b", "strong",
            "i", "em", "span", "br"
        }:
            parts.append(
                child.get_text(" ", strip=True)
            )

    text = " ".join(
        " ".join(parts).split()
    )

    start = re.search(
        r"\bis an issue of the series\b",
        text,
        re.I
    )

    if start:
        text = text[start.start():]

    if not re.search(
        r"\bcover date of\b"
        r"|\bit was published on\b",
        text,
        re.I
    ):
        return (
            "", "", "", "", "",
            "no_date_phrase"
        )

    found = {}

    date_fields = [
        (
            "publication",
            r"\bit was published on\s+"
        ),
        (
            "cover",
            r"\bcover date of\s+"
        ),
    ]

    for field, phrase in date_fields:

        matches = re.finditer(
            phrase + DATE_PATTERN + r"(?!\d)",
            text,
            re.I
        )

        values = {
            parse_date(match.group(1))
            for match in matches
        }

        values.discard(("", ""))

        if len(values) > 1:
            return (
                "", "", "", "", "",
                f"conflicting_{field}_dates"
            )

        found[field] = next(
            iter(values),
            ("", "")
        )

    publication, pub_precision = found[
        "publication"
    ]

    cover, cover_precision = found[
        "cover"
    ]

    if publication:
        return (
            publication[:4],
            "publication",
            publication,
            pub_precision,
            cover,
            "dated"
        )

    if cover and not re.search(
        r"\bit was published on\b",
        text,
        re.I
    ):
        return (
            cover[:4],
            "cover_fallback",
            "",
            "",
            cover,
            "dated"
        )

    if cover:
        return (
        "",                 # network_year
        "",                 # date_source
        "",                 # publication_date
        "",                 # publication_precision
        cover,              # cover_date
        "publication_unparsed_review"
    )

    return (
        "", "", "", "", "",
        "unparsed_review"
    )


def extract_date_task(task):
   #Extract one comic's date in a worker process, 
   #I found this really helped me to speed the process up so I used it here as well

    source, path_string = task

    if path_string is None:
        return (
            source,
            None,
            (
                "", "", "", "", "",
                "missing_or_ambiguous_html"
            )
        )

    try:
        result = extract_date(
            Path(path_string)
        )

    except Exception as error:
        result = (
            "", "", "", "", "",
            "parse_error: " + str(error)[:150]
        )

    return source, path_string, result


#===========================================================
# CSV EXPORT
#===========================================================

def export_csv(connection, filename, sql):

    cursor = connection.execute(sql)

    with filename.open(
        "w",
        encoding="utf-8",
        newline=""
    ) as file:

        writer = csv.writer(file)

        writer.writerow([
            column[0]
            for column in cursor.description
        ])

        while True:
            rows = cursor.fetchmany(10000)

            if not rows:
                break

            writer.writerows(rows)


#============================================================
# VALIDATE INPUTS
#============================================================

def validate_inputs():

    if not INPUT_FILE.is_file():
        raise FileNotFoundError(
            f"Characters CSV not found:\n{INPUT_FILE}"
        )

    if not HTML_FOLDER.is_dir():
        raise FileNotFoundError(
            f"HTML directory not found:\n{HTML_FOLDER}"
        )

    if (
        REPAIRED_HTML_FOLDER is not None
        and not REPAIRED_HTML_FOLDER.is_dir()
    ):
        raise FileNotFoundError(
            "Repaired HTML directory not found:\n"
            f"{REPAIRED_HTML_FOLDER}"
        )


#===========================================================
# BUILD NETWORK
#============================================================

def main():

    validate_inputs()

    OUTPUT_FOLDER.mkdir(
        parents=True,
        exist_ok=True
    )

    database_path = (
        OUTPUT_FOLDER
        / "network_build.sqlite"
    )

    if (
        PROTECT_EXISTING_DATABASE
        and database_path.exists()
    ):
        raise RuntimeError(
            "Output database already exists:\n"
            f"{database_path}\n\n"
            "Choose a new OUTPUT_FOLDER for this run."
        )

    print("=" * 60)
    print("DC COMICS CO-APPEARANCE NETWORK")
    print("ORIGINAL CHARACTER IDENTITY TEST")
    print("=" * 60)

    print(f"Input CSV: {INPUT_FILE}")
    print(f"HTML folder: {HTML_FOLDER}")
    print(f"Output: {OUTPUT_FOLDER}")

    connection = sqlite3.connect(
        database_path
    )

    connection.execute(
        "PRAGMA journal_mode=WAL"
    )

    connection.execute(
        "PRAGMA synchronous=NORMAL"
    )

    connection.execute(
        "PRAGMA temp_store=FILE"
    )

    connection.executescript("""
        CREATE TABLE nodes (
            node_id TEXT PRIMARY KEY,
            node_label TEXT,
            entity_kind TEXT,
            entity_url TEXT
        );

        CREATE TABLE appearances (
            source_file TEXT,
            story TEXT,
            node_id TEXT,
            PRIMARY KEY (
                source_file,
                story,
                node_id
            )
        );

        CREATE TABLE comic_dates (
            source_file TEXT PRIMARY KEY,
            network_year INTEGER,
            date_source TEXT,
            publication_date TEXT,
            publication_precision TEXT,
            cover_date TEXT,
            date_status TEXT,
            html_path_used TEXT
        );
    """)

    counts = Counter()

    node_batch = []
    appearance_batch = []

    #========================================================
    # READ CHARACTER DATA
    #========================================================

    print("\n Reading character CSV...")

    with INPUT_FILE.open(
        encoding="utf-8-sig",
        newline=""
    ) as file:

        reader = csv.DictReader(file)

        required = {
            "source_file",
            "story",
            "entity_name",
            "entity_display_name",
            "entity_url",
            "entity_kind",
            "is_exact_duplicate",
            "mentioned_only",
            "listed_as",
        }

        missing = required - set(
            reader.fieldnames or []
        )

        if missing:
            raise ValueError(
                f"Missing CSV columns: {sorted(missing)}"
            )

        for row in reader:

            counts["input_rows"] += 1

            kind = tidy(
                row["entity_kind"]
            ).casefold()

            if kind not in INCLUDED_ENTITY_KINDS:
                counts["excluded_kind"] += 1
                continue

            if (
                EXCLUDE_EXACT_DUPLICATES
                and yes(row["is_exact_duplicate"])
            ):
                counts["excluded_duplicate"] += 1
                continue

            if (
                not INCLUDE_MENTIONED_ONLY
                and yes(row["mentioned_only"])
            ):
                counts["excluded_mentioned_only"] += 1
                continue

            if (
                tidy(row["listed_as"])
                not in APPEARANCE_LABELS
            ):
                counts["excluded_category"] += 1
                continue

            source = normalize_source(
                row["source_file"]
            )

            story = tidy(
                row["story"]
            )

            if not source or not story:
                counts["excluded_missing_story"] += 1
                continue

            node = node_for(row)

            if not node:
                counts["excluded_blank_identity"] += 1
                continue

            node_batch.append((
                node,
                tidy(row["entity_display_name"])
                or tidy(row["entity_name"]),
                kind,
                tidy(row["entity_url"])
            ))

            appearance_batch.append((
                source,
                story,
                node
            ))

            counts["retained_rows"] += 1

            if yes(row["mentioned_only"]):
                counts["retained_mentioned_only"] += 1

            if len(appearance_batch) >= BATCH_SIZE:

                with connection:
                    connection.executemany(
                        "INSERT OR IGNORE INTO nodes "
                        "VALUES (?,?,?,?)",
                        node_batch
                    )

                    connection.executemany(
                        "INSERT OR IGNORE INTO appearances "
                        "VALUES (?,?,?)",
                        appearance_batch
                    )

                node_batch.clear()
                appearance_batch.clear()

            if counts["input_rows"] % 100000 == 0:
                print(
                    f"Processed "
                    f"{counts['input_rows']:,} rows",
                    flush=True
                )

    with connection:
        connection.executemany(
            "INSERT OR IGNORE INTO nodes VALUES (?,?,?,?)",
            node_batch
        )

        connection.executemany(
            "INSERT OR IGNORE INTO appearances VALUES (?,?,?)",
            appearance_batch
        )

    print(
        f"Retained rows: {counts['retained_rows']:,}"
    )

    #========================================================
    # INDEX HTML FILES
    #=======================================================

    print("\nIndexing HTML files...")

    html_paths = {}

    folders = [
        HTML_FOLDER,
        REPAIRED_HTML_FOLDER
    ]

    for folder in folders:

        if folder is None:
            continue

        for path in folder.rglob("*"):

            if (
                path.is_file()
                and path.suffix.casefold()
                in {".html", ".htm"}
            ):

                relative = path.relative_to(
                    folder
                ).as_posix()

                html_paths[relative] = path

    by_filename = defaultdict(list)

    for relative in html_paths:

        filename = Path(
            relative
        ).name.casefold()

        by_filename[filename].append(
            relative
        )

    print(
        f"HTML files indexed: {len(html_paths):,}"
    )

    #=======================================================
    # EXTRACT COMIC YEARS
    #=======================================================

    print("\nExtracting publication years...")

    sources = connection.execute("""
        SELECT DISTINCT source_file
        FROM appearances
        ORDER BY source_file
    """)

    def date_tasks():

        for (source,) in sources:

            path = html_paths.get(source)

            if path is None:
                candidates = by_filename.get(
                    Path(source).name.casefold(),
                    []
                )

                if len(candidates) == 1:
                    path = html_paths[candidates[0]]

            yield (
                source,
                str(path) if path is not None else None
            )

    date_batch = []

    print(
        f"Date extraction workers: {DATE_WORKERS}",
        flush=True
    )

    with ProcessPoolExecutor(
        max_workers=DATE_WORKERS
    ) as executor:

        results = executor.map(
            extract_date_task,
            date_tasks(),
            chunksize=DATE_CHUNKSIZE
        )

        for i, (source, path_string, result) in enumerate(
            results, 1
        ):

            (
                year,
                date_source,
                publication,
                precision,
                cover,
                status
            ) = result

            if year:
                counts["dated_comics"] += 1
            else:
                counts["undated_comics"] += 1

            date_batch.append((
                source,
                int(year) if year else None,
                date_source,
                publication,
                precision,
                cover,
                status,
                path_string or ""
            ))

            if len(date_batch) >= 1000:

                with connection:
                    connection.executemany(
                        "INSERT INTO comic_dates "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        date_batch
                    )

                date_batch.clear()

            if i % 5000 == 0:
                print(
                    f"HTML dates checked: {i:,}",
                    flush=True
                )

    with connection:
        connection.executemany(
            "INSERT INTO comic_dates "
            "VALUES (?,?,?,?,?,?,?,?)",
            date_batch
        )

    print(
        f"Dated comics: {counts['dated_comics']:,}"
    )

    print(
        f"Undated comics: {counts['undated_comics']:,}"
    )

    #=======================================================
    #BUILD CO-APPEARANCE EDGES
    #========================================================

    print("\nBuilding co-appearance edges...")

    connection.executescript("""
        CREATE INDEX appearances_by_story
        ON appearances (
            source_file,
            story,
            node_id
        );

        CREATE TABLE pair_story AS
        SELECT
            a.source_file,
            a.story,
            a.node_id AS source,
            b.node_id AS target,
            d.network_year
        FROM appearances a
        JOIN appearances b
            ON a.source_file = b.source_file
            AND a.story = b.story
            AND a.node_id < b.node_id
        JOIN comic_dates d
            ON d.source_file = a.source_file;

        CREATE INDEX pair_story_pair
        ON pair_story (
            source,
            target
        );

        CREATE INDEX pair_story_year
        ON pair_story (
            network_year,
            source,
            target
        );
    """)

    #========================================================
    #EXPORT NODES
    #========================================================

    print("\nExporting nodes...")

    export_csv(
        connection,
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
            ON d.source_file = a.source_file

        GROUP BY n.node_id
        ORDER BY n.node_id
        """
    )

    #========================================================
    # EXPORT OVERALL EDGES
    #========================================================

    print("\nExporting overall edges...")

    export_csv(
        connection,
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

    #========================================================
    # EXPORT EDGES BY YEAR
    #========================================================

    print("\nSTEP 7: Exporting yearly edges...")

    export_csv(
        connection,
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

    #========================================================
    #EXPORT STORY APPEARANCES
    #=======================================================

    print("\nExporting story appearances...")

    export_csv(
        connection,
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

    #========================================================
    #EXPORT COMIC DATES
    #=======================================================

    print("\nSTEP 9: Exporting comic dates...")

    export_csv(
        connection,
        OUTPUT_FOLDER / "comic_dates.csv",
        """
        SELECT *
        FROM comic_dates
        ORDER BY source_file
        """
    )

    #========================================================
    #SUMMARY
    #=======================================================

    print("\n" + "=" * 60)
    print("NETWORK SUMMARY")
    print("=" * 60)

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
    }

    for label, sql in queries.items():

        result = connection.execute(
            sql
        ).fetchone()[0]

        print(
            f"{label}: {result:,}"
        )

    print("\nClassification counts:")

    for key, value in counts.items():
        print(
            f"  {key}: {value:,}"
        )

    print("\nFiles saved to:")
    print(OUTPUT_FOLDER.resolve())

    print("\nFinished successfully.")

    connection.close()


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()
