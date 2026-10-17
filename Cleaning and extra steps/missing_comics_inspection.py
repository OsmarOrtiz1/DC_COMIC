from pathlib import Path
from collections import Counter, defaultdict
from datetime import datetime
from urllib.parse import urljoin
import csv
import hashlib
import os
import re
import shutil
import tempfile

import pandas as pd
from bs4 import BeautifulSoup


# ============================================================
# SETTINGS
# ============================================================

PROJECT_ROOT = Path(
    r"C:\Users\26472724\OneDrive - University of Limerick"
    r"\Desktop\PhD UL\DC_NETWORK"
)

HTML_FOLDER = PROJECT_ROOT / "comic_pages"
REPAIR_FOLDER = PROJECT_ROOT / "data" / "comic_pages"
OUTPUT_FILE = PROJECT_ROOT / "data" / "comic_characters_cleaned.csv"
CHARACTER_URL_FILE = PROJECT_ROOT / "data" / "dc_characters.csv"
ASSOCIATION_KEYWORD_FILE = (
    PROJECT_ROOT / "data" / "keywords_associations.csv"
)

BASE_URL = "https://dc.fandom.com"
APPLY = True

BATCH_COLUMN = "recovery_batch"

APPEARANCE_LABELS = {
    "Featured Characters",
    "Supporting Characters",
    "Antagonists",
    "Other Characters",
    "Characters",
    "Main Characters",
    "Guest Characters",
}

BASE_COLUMNS = [
    "record_id",
    "source_file",
    "comic",
    "story",
    "entity_display_name",
    "entity_name",
    "entity_url",
    "listed_as",
    "mentioned_only",
    "entity_kind",
    "association_category",
    "classification_method",
    "classification_flags",
    "network_eligible",
    "is_exact_duplicate",
    "record_status",
]


# ============================================================
# NORMALISATION
# ============================================================

def clean_text(value):
    if value is None:
        return ""

    value = str(value)

    # Remove numeric citation markers, not arbitrary bracketed names.
    value = re.sub(r"\[\d+\]", "", value)

    return " ".join(value.split()).strip()


def normalise_url(url):
    url = clean_text(url)

    if not url:
        return ""

    if url.startswith("/"):
        url = urljoin(BASE_URL, url)

    url = url.split("#", 1)[0]
    url = url.split("?", 1)[0]

    return url.rstrip("/")


def normalise_name(name):
    # Preserve parenthetical identity/universe information.
    return clean_text(name)


def normalise_match_name(name):
    return normalise_name(name).casefold()


def filename_key(value):
    value = str(value or "").strip().replace("\\", "/")

    while value.startswith("./"):
        value = value[2:]

    return value


def create_record_id(source_file, story, name, url):
    value = "|".join([
        clean_text(source_file),
        clean_text(story),
        normalise_name(name),
        normalise_url(url),
    ])

    return hashlib.sha256(
        value.encode("utf-8")
    ).hexdigest()[:16]


def file_signature(path):
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns


# ============================================================
# REFERENCE FILES
# ============================================================

def find_column(frame, possible_names):
    columns = {
        str(column).casefold().strip(): column
        for column in frame.columns
    }

    for name in possible_names:
        if name.casefold() in columns:
            return columns[name.casefold()]

    raise ValueError(
        f"Expected one of {possible_names}; "
        f"found {list(frame.columns)}"
    )


def load_character_urls(path):
    frame = pd.read_csv(
        path,
        dtype=str,
        keep_default_na=False,
        encoding="utf-8-sig",
    )

    column = find_column(
        frame,
        ["character_url", "url", "wiki_url"],
    )

    return {
        normalise_url(value)
        for value in frame[column]
        if normalise_url(value)
    }


def load_association_reference(path):
    frame = pd.read_csv(
        path,
        dtype=str,
        keep_default_na=False,
        encoding="utf-8-sig",
    )

    keyword_column = find_column(frame, ["keyword"])
    url_column = find_column(frame, ["url"])
    category_column = find_column(frame, ["category"])

    by_url = defaultdict(set)
    keywords = set()

    for _, row in frame.iterrows():
        keyword = normalise_match_name(row[keyword_column])
        url = normalise_url(row[url_column])
        category = clean_text(row[category_column])

        if url:
            by_url[url].add(category)

        if keyword:
            keywords.add((keyword, category))

    # Compile patterns once.
    patterns = [
        (
            re.compile(
                r"(?<!\w)" + re.escape(keyword) + r"(?!\w)",
                flags=re.I,
            ),
            keyword,
            category,
        )
        for keyword, category in sorted(keywords)
    ]

    return dict(by_url), patterns


# ============================================================
# CLASSIFICATION
# ============================================================

def classify_entity(
    name,
    url,
    mentioned_only,
    character_urls,
    association_urls,
    association_patterns,
):
    display_name = clean_text(name)
    stable_url = normalise_url(url)

    result = {
        "entity_kind": "",
        "association_category": "",
        "classification_method": "",
        "classification_flags": "",
        "network_eligible": False,
    }

    if not re.search(r"[a-z0-9]", display_name.casefold()):
        result.update(
            entity_kind="invalid",
            classification_method="invalid_display_name",
            classification_flags="no_alphanumeric_characters",
        )
        return result

    # Preserve your classification priority:
    # association URL -> keyword review -> character URL.
    if stable_url in association_urls:
        result.update(
            entity_kind="association",
            association_category="|".join(
                sorted(association_urls[stable_url])
            ),
            classification_method="association_url_match",
        )
        return result

    match_name = normalise_match_name(display_name)

    matches = [
        (keyword, category)
        for pattern, keyword, category in association_patterns
        if pattern.search(match_name)
    ]

    if matches:
        result.update(
            entity_kind="manual_review",
            association_category="|".join(
                sorted({category for _, category in matches})
            ),
            classification_method="association_keyword_candidate",
            classification_flags=(
                "matched_keywords="
                + "|".join(sorted({keyword for keyword, _ in matches}))
            ),
        )
        return result

    if stable_url in character_urls:
        result.update(
            entity_kind="character",
            classification_method="character_url_match",
            network_eligible=not mentioned_only,
        )
        return result

    if stable_url:
        result.update(
            entity_kind="external_url",
            classification_method="url_not_in_reference_lists",
        )
        return result

    result.update(
        entity_kind="unknown",
        classification_method="missing_url",
    )
    return result


# ============================================================
# HTML EXTRACTION
# ============================================================

def get_story_title(text):
    text = clean_text(text)

    match = re.search(
        r'Appearing in\s+["“](.*?)["”]',
        text,
        flags=re.I,
    )

    if match:
        return clean_text(match.group(1))

    return re.sub(
        r"^Appearing in\s+",
        "",
        text,
        flags=re.I,
    ).strip()


def list_item_entity(item):
    # Inspect this item's own label, excluding nested character lists.
    fragment_parts = []

    for child in item.contents:
        if getattr(child, "name", None) in {"ul", "ol"}:
            break
        fragment_parts.append(str(child))

    fragment = BeautifulSoup(
        "".join(fragment_parts),
        "html.parser",
    )

    for footnote in fragment.select("sup.reference"):
        footnote.decompose()

    full_text = clean_text(fragment.get_text(" ", strip=True))

    # Also handles links wrapped in bold/italic tags.
    link = fragment.find("a", href=True)

    if link:
        name = clean_text(link.get_text(" ", strip=True))
        url = normalise_url(link["href"])
    else:
        name = full_text
        url = ""

    return name, url, full_text


def extract_entities(
    path,
    source_file,
    character_urls,
    association_urls,
    association_patterns,
):
    text = path.read_text(encoding="utf-8", errors="replace")

    if not text.strip():
        return [], "empty file"

    soup = BeautifulSoup(text, "html.parser")

    title = (
        soup.title.get_text(" ", strip=True)
        if soup.title else ""
    )

    if any(term in title.casefold() for term in (
        "just a moment",
        "attention required",
        "access denied",
        "verify you are human",
        "connection timed out",
        "internal server error",
    )):
        return [], f"security/server-error page: {title}"

    heading = soup.find("h1") or soup.find("title")
    comic = (
        clean_text(heading.get_text(" ", strip=True))
        if heading else path.stem
    )

    appearing_headings = soup.find_all(
        lambda tag: (
            tag.name in {"h2", "h3"}
            and "appearing in"
            in tag.get_text(" ", strip=True).casefold()
        )
    )

    if not appearing_headings:
        return [], "no appearance heading"

    records = []

    for heading in appearing_headings:
        story = get_story_title(
            heading.get_text(" ", strip=True)
        )

        current_category = None
        element = heading.find_next_sibling()

        while element is not None:
            if element.name in {"h2", "h3"}:
                break

            if element.name == "p":
                possible_category = clean_text(
                    element.get_text(" ", strip=True)
                ).rstrip(":").strip()

                current_category = (
                    possible_category
                    if possible_category in APPEARANCE_LABELS
                    else None
                )

            if element.name in {"ul", "ol"} and current_category:
                # Each list item is visited once, including nested members.
                for item in element.find_all("li"):
                    name, url, full_text = list_item_entity(item)

                    if not name:
                        continue

                    mentioned_only = (
                        "mentioned only" in full_text.casefold()
                    )

                    classification = classify_entity(
                        name,
                        url,
                        mentioned_only,
                        character_urls,
                        association_urls,
                        association_patterns,
                    )

                    records.append({
                        "record_id": create_record_id(
                            source_file, story, name, url
                        ),
                        "source_file": source_file,
                        "comic": comic,
                        "story": story,
                        "entity_display_name": clean_text(name),
                        "entity_name": normalise_name(name),
                        "entity_url": normalise_url(url),
                        "listed_as": current_category,
                        "mentioned_only": mentioned_only,
                        **classification,
                    })

            element = element.find_next_sibling()

    return records, (
        "recovered" if records
        else "appearance heading present, but no records extracted"
    )


# ============================================================
# SAVE SAFELY WITH A RECOVERY LABEL
# ============================================================

def save_recovery(records, columns, original_signature, existing_count):
    if file_signature(OUTPUT_FILE) != original_signature:
        raise RuntimeError(
            "The original CSV changed. Nothing updated."
        )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    batch_label = f"recovery_{timestamp}"

    output_columns = list(columns)

    if BATCH_COLUMN not in output_columns:
        output_columns.append(BATCH_COLUMN)

    backup = OUTPUT_FILE.with_name(
        f"{OUTPUT_FILE.stem}.backup_{timestamp}.csv"
    )

    shutil.copy2(OUTPUT_FILE, backup)
    print(f"\nBackup created: {backup}", flush=True)

    temporary_path = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=OUTPUT_FILE.parent,
            prefix=OUTPUT_FILE.stem + ".",
            suffix=".tmp",
            delete=False,
        ) as destination:
            temporary_path = Path(destination.name)

            writer = csv.DictWriter(
                destination,
                fieldnames=output_columns,
            )
            writer.writeheader()

            with OUTPUT_FILE.open(
                "r", encoding="utf-8-sig", newline=""
            ) as source:
                reader = csv.DictReader(source)

                if reader.fieldnames != columns:
                    raise RuntimeError("Original CSV header changed.")

                for row in reader:
                    row.setdefault(BATCH_COLUMN, "")
                    writer.writerow(row)

            for record in records:
                row = dict(record)
                row[BATCH_COLUMN] = batch_label
                writer.writerow(row)

            destination.flush()
            os.fsync(destination.fileno())

        # Verify that every existing row retains its field values.
        with OUTPUT_FILE.open(
            "r", encoding="utf-8-sig", newline=""
        ) as source, temporary_path.open(
            "r", encoding="utf-8", newline=""
        ) as prepared:
            old_reader = csv.DictReader(source)
            new_reader = csv.DictReader(prepared)

            if new_reader.fieldnames != output_columns:
                raise RuntimeError("Prepared CSV header mismatch.")

            checked_old = 0

            for old_row in old_reader:
                new_row = next(new_reader, None)

                expected = dict(old_row)
                expected.setdefault(BATCH_COLUMN, "")

                if new_row != expected:
                    raise RuntimeError(
                        "An existing row changed during preparation."
                    )

                checked_old += 1

            if checked_old != existing_count:
                raise RuntimeError("Original row count changed.")

            checked_new = 0

            for expected_record in records:
                new_row = next(new_reader, None)
                expected = {
                    column: str(expected_record.get(column, ""))
                    for column in output_columns
                }
                expected[BATCH_COLUMN] = batch_label

                if new_row != expected:
                    raise RuntimeError(
                        "Recovered row validation failed."
                    )

                checked_new += 1

            if next(new_reader, None) is not None:
                raise RuntimeError("Unexpected additional rows.")

        if file_signature(OUTPUT_FILE) != original_signature:
            raise RuntimeError(
                "The original CSV changed during preparation."
            )

        # Replace only after the complete prepared file passes checks.
        os.replace(temporary_path, OUTPUT_FILE)
        temporary_path = None

        print(f"\nRecovery batch: {batch_label}")
        print(f"Existing rows preserved: {checked_old:,}")
        print(f"New rows added: {checked_new:,}")
        print(f"Updated CSV: {OUTPUT_FILE}")

    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


# ============================================================
# MAIN RECOVERY PROCESS
# ============================================================

def main():
    for path in (
        OUTPUT_FILE,
        CHARACTER_URL_FILE,
        ASSOCIATION_KEYWORD_FILE,
    ):
        if not path.is_file():
            raise FileNotFoundError(path)

    if not HTML_FOLDER.is_dir():
        raise FileNotFoundError(HTML_FOLDER)

    if not REPAIR_FOLDER.is_dir():
        raise FileNotFoundError(
            f"Repair HTML folder not found: {REPAIR_FOLDER}"
        )

    print(f"HTML folder: {HTML_FOLDER.resolve()}")
    print(f"Existing CSV: {OUTPUT_FILE.resolve()}")
    print(f"APPLY: {APPLY}")

    original_signature = file_signature(OUTPUT_FILE)

    represented = set()
    existing_count = 0

    with OUTPUT_FILE.open(
        "r", encoding="utf-8-sig", newline=""
    ) as file:
        reader = csv.DictReader(file)
        columns = reader.fieldnames

        if not columns or len(columns) != len(set(columns)):
            raise ValueError("Missing or duplicate CSV column names.")

        expected = set(BASE_COLUMNS)
        actual = set(columns) - {BATCH_COLUMN}

        if actual != expected:
            raise ValueError(
                "Unexpected CSV schema. "
                f"Missing: {sorted(expected - actual)}; "
                f"extra: {sorted(actual - expected)}"
            )

        for row in reader:
            if None in row or any(
                value is None for value in row.values()
            ):
                raise ValueError(
                    f"Malformed CSV record near row {existing_count + 2}."
                )

            existing_count += 1

            source = filename_key(row["source_file"])

            if not source:
                raise ValueError(
                    "Existing CSV contains a blank source_file."
                )

            represented.add(source)

    html_files = {}
    by_basename = defaultdict(list)

    for path in sorted(HTML_FOLDER.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".html", ".htm"}:
            continue

        source = filename_key(
            path.relative_to(HTML_FOLDER).as_posix()
        )

        if source in html_files:
            raise ValueError(f"Ambiguous relative filename: {source}")

        html_files[source] = path
        by_basename[path.name.casefold()].append(source)

    if not html_files:
        raise ValueError("No HTML files found.")

    # Prevent accidental recovery against the wrong collection.
    unresolved = represented - set(html_files)

    if unresolved:
        print("\nCSV source files not found under HTML_FOLDER:")
        for source in sorted(unresolved)[:20]:
            print(f"  {source}")

        raise ValueError(
            "Check the HTML folder and source_file paths. "
            "No files changed."
        )

    represented_basenames = {
        source.rsplit("/", 1)[-1].casefold()
        for source in represented
    }

    missing = []
    ambiguous = []

    for source in sorted(set(html_files) - represented):
        basename = html_files[source].name.casefold()

        if (
            len(by_basename[basename]) > 1
            or basename in represented_basenames
        ):
            ambiguous.append(source)
        else:
            missing.append(source)

    print(f"\nExisting rows: {existing_count:,}")
    print(f"HTML files: {len(html_files):,}")
    print(f"Missing files to inspect: {len(missing):,}")
    print(f"Ambiguous duplicate paths skipped: {len(ambiguous):,}")

    for source in ambiguous:
        print(f"  REVIEW DUPLICATE PATH: {source}")

    character_urls = load_character_urls(CHARACTER_URL_FILE)

    association_urls, association_patterns = (
        load_association_reference(ASSOCIATION_KEYWORD_FILE)
    )

    recovered = []
    errors = []
    no_records = Counter()
    recovered_files = 0

    duplicate_columns = [
        "source_file",
        "story",
        "entity_name",
        "entity_url",
        "listed_as",
    ]

    for number, source in enumerate(missing, start=1):
        try:
            repair_path = REPAIR_FOLDER / html_files[source].name

            if repair_path.is_file():
                extraction_path = repair_path
                print(f"Using repaired HTML : {repair_path}", flush=True)
            else:
                extraction_path = html_files[source]

            records, status = extract_entities(
                extraction_path,
                source,
                character_urls,
                association_urls,
                association_patterns,
            )

            if not records:
                no_records[status] += 1
                print(
                    f"[{number}/{len(missing)}] "
                    f"NO RECORDS: {source} — {status}",
                    flush=True,
                )
                continue

            prepared = []
            seen = set()

            for record in records:
                key = tuple(
                    record[column]
                    for column in duplicate_columns
                )

                record["is_exact_duplicate"] = key in seen
                seen.add(key)

                record["record_status"] = (
                    "manual_review"
                    if record["entity_kind"]
                    in {"invalid", "manual_review"}
                    else "retained"
                )

                if set(record) != set(BASE_COLUMNS):
                    raise ValueError("Extractor record schema mismatch.")

                prepared.append(record)

            recovered.extend(prepared)
            recovered_files += 1

            print(
                f"[{number}/{len(missing)}] "
                f"RECOVERED: {source} — {len(prepared):,} rows",
                flush=True,
            )

            for record in prepared[:3]:
                print(
                    f"    {record['entity_name']} "
                    f"[{record['entity_kind']}]"
                )

        except Exception as error:
            errors.append(source)
            print(
                f"ERROR: {source}\n"
                f"  {type(error).__name__}: {error}",
                flush=True,
            )

    print("\nRECOVERY SUMMARY")
    print(f"Comics recovered: {recovered_files:,}")
    print(f"Rows ready to add: {len(recovered):,}")
    print(f"Extraction errors: {len(errors):,}")

    for reason, count in no_records.items():
        print(f"  No records — {reason}: {count:,}")

    print("\nRecovered classifications:")
    for kind, count in Counter(
        row["entity_kind"] for row in recovered
    ).most_common():
        print(f"  {kind}: {count:,}")

    if not APPLY:
        print("\nPREVIEW ONLY. No files created or changed.")
        print(
            "Review the results, then set APPLY = True "
            "to save the recovered rows with a recovery_batch label."
        )
        return

    if errors:
        raise RuntimeError(
            "Resolve extraction errors before applying. "
            "No files changed."
        )

    if not recovered:
        print("\nNo new rows to save. Nothing changed.")
        return

    save_recovery(
        recovered,
        columns,
        original_signature,
        existing_count,
    )

    print("\nCOMICS ADDED:")
    for source, count in sorted(
        Counter(row["source_file"] for row in recovered).items()
    ):
        print(f"  {source}: {count:,} rows")


if __name__ == "__main__":
    main()