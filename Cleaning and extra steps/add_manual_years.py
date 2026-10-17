from pathlib import Path
from collections import defaultdict
from datetime import datetime
from itertools import groupby
from types import SimpleNamespace

import argparse
import csv
import json
import os
import re
import runpy
import shutil
import sqlite3
import tempfile


csv.field_size_limit(100_000_000)

PROJECT_ROOT = Path(
    r"C:\Users\26472724\OneDrive - University of Limerick"
    r"\Desktop\PhD UL\DC_NETWORK"
)

FILES_TO_UPDATE = [
    "characters_dated.csv",
    "raw_edges_dated.csv",
    "edges.csv",
    "edges_by_year.csv",
    "comic_audit.csv",
]

AGGREGATION_FIELDS = [
    "source", "target", "source_label", "target_label",
    "source_universe", "target_universe",
    "comic_title", "story_title", "html_source_file",
    "context_label", "edge_type", "date_source",
    "network_year", "evidence_category",
]

# These three truncated entries were checked against your audit.
TRUNCATED_MATCHES = {
    (
        "Adventures with the DC Super-Heroes: "
        "Energy Safety & Efficiency (2002)"
    ): "Adventures with the DC Super-Heroes Energy Safety & Efficiency.html",

    (
        "Justice League Goes Inside the NBA - "
        "All Star Edition (2014) #1"
    ): "Justice League Goes Inside the NBA - All Star Edition Vol 1 1.html",

    (
        "Lego DC Comics Super Heroes: "
        "Batman v Superman: Dawn of Justice (2016)"
    ): "Lego DC Comics Super Heroes Batman v Superman Dawn of Justice.html",
}


# ============================================================
# HELPERS
# ============================================================

def normalize(value):
    return " ".join(str(value).casefold().split()).rstrip(" >")


def basename(value):
    return str(value).replace("\\", "/").rsplit("/", 1)[-1]


def values(value):
    return {part for part in str(value or "").split("|") if part}


def read_rows(path, required=()):
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = set(required) - set(reader.fieldnames or [])

        if missing:
            raise ValueError(
                f"{path.name}: missing columns {sorted(missing)}"
            )

        for row in reader:
            if None in row or any(v is None for v in row.values()):
                raise ValueError(
                    f"Malformed row in {path.name}, "
                    f"ending at line {reader.line_num}"
                )
            yield row


def require_file(path, description):
    path = Path(path)

    while not path.is_file():
        print(f"\nCannot find {description}:\n{path}")
        entered = input(
            "Paste its full file path here, without r before the quotes: "
        ).strip().strip("\"'")
        path = Path(entered)

    return path


def choose_run(base, requested):
    def usable(path):
        return (
            path.is_dir()
            and all((path / name).is_file() for name in FILES_TO_UPDATE)
            and any(
                (path / name).is_file()
                for name in ("PARTIAL.json", "COMPLETE.json")
            )
            and not any(
                (path / name).exists()
                for name in (
                    "INCOMPLETE.txt",
                    "FAILED.txt",
                    "RECOVERY_IN_PROGRESS.json",
                    "MANUAL_YEARS_IN_PROGRESS.json",
                )
            )
        )

    if requested:
        run = Path(requested)
        if not usable(run):
            raise ValueError(
                f"This is not a finished, usable output folder:\n{run}"
            )
        return run

    candidates = (
        sorted(
            (path for path in base.iterdir() if usable(path)),
            key=lambda path: path.name,
        )
        if base.is_dir() else []
    )

    if len(candidates) == 1:
        return candidates[0]

    if candidates:
        print("\nChoose the output folder containing the recovered results:")
        for number, path in enumerate(candidates, 1):
            print(f"{number}. {path.name}")

        while True:
            answer = input("Folder number: ").strip()
            if answer.isdigit() and 1 <= int(answer) <= len(candidates):
                return candidates[int(answer) - 1]

    entered = input(
        "\nPaste the full path of the recovered output folder: "
    ).strip().strip("\"'")

    run = Path(entered)
    if not usable(run):
        raise ValueError(f"Not a finished, usable output folder: {run}")
    return run


# ============================================================
# MATCH MANUAL YEARS
# ============================================================

def match_manual_years(manual_path, audit):
    updates = {}
    unresolved = []
    already_dated = []

    for row in read_rows(
        manual_path,
        ["comic_title  html_file  >", "publication_date"],
    ):
        original_label = row["comic_title  html_file  >"]
        label = normalize(original_label)
        year_text = row["publication_date"].strip()

        if not re.fullmatch(r"\d{4}", year_text):
            raise ValueError(
                f"Invalid manual year {year_text!r}: {original_label}"
            )

        year = int(year_text)

        candidates = [
            comic for comic in audit
            if label.endswith(normalize(basename(comic["source_file"])))
        ]

        # Prevent a short filename such as "Batman Vol 1 1.html"
        # from matching "3-D Batman Vol 1 1.html".
        if candidates:
            longest = max(
                len(normalize(basename(comic["source_file"])))
                for comic in candidates
            )
            candidates = [
                comic for comic in candidates
                if len(normalize(basename(comic["source_file"]))) == longest
            ]
        else:
            for prefix, filename in TRUNCATED_MATCHES.items():
                if label.startswith(normalize(prefix) + " "):
                    candidates = [
                        comic for comic in audit
                        if comic["source_file"] == filename
                    ]
                    break

        if len(candidates) != 1:
            unresolved.append({
                "manual_entry": original_label,
                "year": year,
                "reason": (
                    "No confirmed match" if not candidates
                    else "Multiple audit records match"
                ),
                "candidates": [
                    comic["source_file"] for comic in candidates
                ],
            })
            continue

        comic = candidates[0]
        source = comic["source_file"]

        # Never overwrite an existing network year.
        if str(comic["network_year"]).strip():
            already_dated.append(source)
            continue

        # The confirmed matches in your uploaded audit all had
        # unknown dates and blank publication dates.
        if (
            comic["date_source"] != "unknown"
            or comic["publication_date"].strip()
            or comic["publication_year"].strip()
        ):
            unresolved.append({
                "manual_entry": original_label,
                "year": year,
                "reason": "Existing date information requires review",
                "candidates": [source],
            })
            continue

        if source in updates and updates[source] != year:
            raise ValueError(f"Conflicting manual years for {source}")

        updates[source] = year

    return updates, unresolved, already_dated


# ============================================================
# IDENTIFY NODE APPEARANCES AFFECTED BY THE DATE UPDATE
# Includes nodes in single-entity stories with no edges.
# ============================================================

def affected_node_stories(module, csv_path, updates, recovered_sources):
    affected = defaultdict(set)
    seen_sources = set()

    for _, row in module.read_appearance_rows(csv_path):
        source = module.file_key(row["source_file"])

        if source not in updates:
            continue

        seen_sources.add(source)
        kind = row["entity_kind"].strip().lower()

        # Preserve the filter applied by the classification recovery.
        if kind in module.OTHER_KINDS:
            continue

        if kind not in module.CHARACTER_KINDS | module.OTHER_KINDS:
            raise ValueError(f"Unexpected entity_kind in {source}: {kind}")

        if module.boolean_value(
            row["is_exact_duplicate"], "is_exact_duplicate"
        ):
            continue


        if row["listed_as"] not in module.APPEARANCE_LABELS:
            continue

        key = module.story_key(row["story"])
        record = module.make_entity(row, source, key)

        affected[record["node_id"]].add((source, key))

    missing_sources = set(updates) - seen_sources
    if missing_sources:
        raise ValueError(
            "The appearance CSV does not contain these audit sources: "
            + repr(sorted(missing_sources))
        )

    return affected


def update_nodes(module, run, stage, affected, updates):
    pending = set(affected)
    updated_count = 0
    node_count = 0

    with (stage / "characters_dated.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=module.NODE_FIELDS)
        writer.writeheader()

        for row in read_rows(
            run / "characters_dated.csv", module.NODE_FIELDS
        ):
            node_count += 1
            node_id = row["node_id"]

            if node_id in affected:
                if node_id not in pending:
                    raise ValueError(f"Duplicate node ID: {node_id}")

                stories = affected[node_id]
                old_undated = int(row["undated_story_count"] or 0)

                if old_undated < len(stories):
                    raise ValueError(
                        "Node date counts do not match the appearance CSV: "
                        f"{node_id}. No outputs have been replaced."
                    )

                years = {
                    int(year) for year in values(row["observed_years"])
                }
                years.update(updates[source] for source, _ in stories)

                row["observed_years"] = "|".join(
                    str(year) for year in sorted(years)
                )
                row["first_observed_year"] = min(years)
                row["last_observed_year"] = max(years)
                row["undated_story_count"] = old_undated - len(stories)

                sources = values(row["date_sources"])
                sources.add("manual_publication")

                if row["undated_story_count"] == 0:
                    sources.discard("unknown")

                row["date_sources"] = "|".join(sorted(sources))

                # Appearance counts and identities stay unchanged.
                pending.remove(node_id)
                updated_count += 1

            writer.writerow(row)

    if pending:
        raise ValueError(
            "Some affected CSV identities are absent from the node output. "
            "Check that the CSV and edge_finder.py match this run. Examples: "
            + repr(sorted(pending)[:5])
        )

    return node_count, updated_count


# ============================================================
# UPDATE RAW DATES AND REBUILD EDGE DATE SUMMARIES
# ============================================================

def aggregate_database(module, database, annual):
    cursor = database.execute(
        "SELECT payload FROM evidence ORDER BY source, target, year"
    )
    decoded = (json.loads(row[0]) for row in cursor)

    key = (
        (lambda edge: (
            edge["source"], edge["target"], edge["network_year"]
        ))
        if annual else
        (lambda edge: (edge["source"], edge["target"]))
    )

    for _, group in groupby(decoded, key):
        yield module.aggregate_pair(group, annual)


def update_edges(module, run, stage, updated_audit):
    raw_count = 0
    changed_count = 0
    undated_count = 0

    with tempfile.TemporaryDirectory(prefix="dc_manual_years_") as temp:
        database = sqlite3.connect(Path(temp) / "aggregation.sqlite")

        try:
            database.execute(
                "CREATE TABLE evidence "
                "(source TEXT, target TEXT, year INTEGER, payload TEXT)"
            )
            database.execute("PRAGMA cache_size = -65536")
            batch = []

            with (stage / "raw_edges_dated.csv").open(
                "w", encoding="utf-8", newline=""
            ) as handle:
                writer = csv.DictWriter(
                    handle, fieldnames=module.RAW_FIELDS
                )
                writer.writeheader()

                for row in read_rows(
                    run / "raw_edges_dated.csv", module.RAW_FIELDS
                ):
                    source = module.file_key(row["html_source_file"])

                    if source in updated_audit:
                        if str(row["network_year"]).strip():
                            raise ValueError(
                                f"Audit and raw-edge dates disagree for {source}. "
                                "Existing outputs have not been replaced."
                            )

                        row.update({
                            field: updated_audit[source][field]
                            for field in module.DATE_FIELDS
                        })
                        changed_count += 1

                    writer.writerow(row)
                    raw_count += 1

                    payload = {
                        field: row[field]
                        for field in AGGREGATION_FIELDS
                    }

                    text = str(payload["network_year"]).strip()
                    year = int(text) if text else None
                    payload["network_year"] = (
                        year if year is not None else ""
                    )

                    if year is None:
                        undated_count += 1

                    batch.append((
                        payload["source"],
                        payload["target"],
                        year,
                        json.dumps(payload),
                    ))

                    if len(batch) >= 10_000:
                        database.executemany(
                            "INSERT INTO evidence VALUES (?, ?, ?, ?)",
                            batch,
                        )
                        database.commit()
                        batch.clear()

                    if raw_count % 250_000 == 0:
                        print(
                            f"  Processed {raw_count:,} evidence rows",
                            flush=True,
                        )

            if batch:
                database.executemany(
                    "INSERT INTO evidence VALUES (?, ?, ?, ?)",
                    batch,
                )
                database.commit()

            print("Rebuilding edge summaries...", flush=True)

            database.execute(
                "CREATE INDEX pairs ON evidence(source, target, year)"
            )
            database.commit()

            edge_count = module.write_csv(
                stage / "edges.csv",
                aggregate_database(module, database, False),
                module.AGGREGATE_FIELDS,
            )

            annual_count = module.write_csv(
                stage / "edges_by_year.csv",
                aggregate_database(module, database, True),
                ["network_year", *module.AGGREGATE_FIELDS],
            )

        finally:
            database.close()

    return {
        "raw_edges": raw_count,
        "raw_edges_updated": changed_count,
        "raw_edges_without_year": undated_count,
        "aggregate_edges": edge_count,
        "annual_edges": annual_count,
    }


# ============================================================
# BACK UP AND REPLACE ORIGINALS
# Staged files are copied, not moved, so they remain intact
# if Windows blocks a replacement.
# ============================================================

def install(run, stage, names, stamp):
    backup = run / f"backup_before_manual_years_{stamp}"
    backup.mkdir()

    for name in names:
        shutil.copy2(run / name, backup / name)

    marker = run / "MANUAL_YEARS_IN_PROGRESS.json"
    marker.write_text(
        json.dumps({
            "backup": str(backup),
            "stage": str(stage),
            "files": names,
        }, indent=2),
        encoding="utf-8",
    )

    try:
        for name in names:
            pending = run / f".{name}.{stamp}.pending"
            shutil.copy2(stage / name, pending)
            os.replace(pending, run / name)

    except BaseException:
        rollback_errors = []

        # Attempt every restoration even if one file is locked.
        for name in names:
            try:
                shutil.copy2(backup / name, run / name)
            except OSError as error:
                rollback_errors.append(f"{name}: {error}")

        if rollback_errors:
            print("\nSome original files could not be restored:")
            for message in rollback_errors:
                print(message)
            print(f"Keep the marker and restore ALL files from:\n{backup}")
        else:
            marker.unlink()
            print("\nOriginal files restored successfully.")

        raise

    else:
        marker.unlink()

    return backup


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Fill confirmed manual years in existing network outputs."
    )
    parser.add_argument("--script", type=Path)
    parser.add_argument("--manual", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    script_candidates = [
        Path(__file__).resolve().with_name("edge_finder.py"),
        PROJECT_ROOT / "data" / "edge_finder.py",
        PROJECT_ROOT / "edge_finder.py",
    ]

    script = args.script or next(
        (path for path in script_candidates if path.is_file()),
        script_candidates[0],
    )
    script = require_file(script, "the original edge_finder.py")

    module = SimpleNamespace(**runpy.run_path(
        str(script),
        run_name="manual_year_update_import",
    ))

    manual = require_file(
        args.manual
        or Path(module.PROJECT_ROOT) / "network_dated" / "year_manual.csv",
        "year_manual.csv",
    )

    run = choose_run(
        PROJECT_ROOT / "network_dated_with_mentions",
        args.output
        )

    status_files = [
        run / name
        for name in ("PARTIAL.json", "COMPLETE.json")
        if (run / name).is_file()
    ]
    if len(status_files) != 1:
        raise ValueError("Expected exactly one COMPLETE/PARTIAL status file.")

    status_path = status_files[0]
    summary = json.loads(status_path.read_text(encoding="utf-8"))

    # Check basic write access before doing the lengthy work.
    for name in [*FILES_TO_UPDATE, status_path.name]:
        with (run / name).open("r+b"):
            pass

    audit_fields = [
        "source_file", "html_path_used", "html_origin", "stories",
        *module.DATE_FIELDS,
    ]
    audit = list(read_rows(run / "comic_audit.csv", audit_fields))

    if len({row["source_file"] for row in audit}) != len(audit):
        raise ValueError("Duplicate source_file values in comic_audit.csv.")

    updates, unresolved, already_dated = match_manual_years(manual, audit)

    print(f"\nUpdating this existing run:\n{run}")
    print(f"Confirmed missing years to fill: {len(updates):,}")
    print(f"Manual entries left for review: {len(unresolved):,}")
    print(f"Already-dated matches left unchanged: {len(already_dated):,}")

    if not updates:
        print("No confirmed missing years need updating.")
        return

    # Keep the classification-recovery policy when reconstructing
    # affected node appearances.
    recovered_sources = set()
    report_path = run / "recovery_report.json"

    if report_path.is_file():
        report = json.loads(report_path.read_text(encoding="utf-8"))
        recovered_sources.update(report.get("recovered_comics", []))

    elif summary.get("last_recovery"):
        raise ValueError(
            "This run records a classification recovery, but its "
            "recovery_report.json is missing. Restore that report first."
        )

    # Include earlier recovery reports if they remain in local backups.
    for path in run.glob("backup_before_recovery_*/recovery_report.json"):
        report = json.loads(path.read_text(encoding="utf-8"))
        recovered_sources.update(report.get("recovered_comics", []))

    csv_path = require_file(
        Path(module.APPEARANCES_CSV),
        "the appearance CSV used for this run",
    )

    print("Identifying affected node appearances...", flush=True)
    affected = affected_node_stories(
        module, csv_path, updates, recovered_sources
    )

    updated_audit = {}

    for row in audit:
        source = row["source_file"]
        if source not in updates:
            continue

        year = updates[source]
        old_evidence = row["date_evidence"]

        row.update({
            "publication_date": str(year),
            "publication_precision": "year",
            "publication_year": str(year),
            "network_year": str(year),
            "date_source": "manual_publication",
            "date_status": "manual_year",
            "date_evidence": (
                f"{old_evidence} | " if old_evidence else ""
            ) + f"Manual publication year {year} from {manual.name}",
        })
        updated_audit[source] = row

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    stage = run / f"_manual_year_stage_{stamp}"
    stage.mkdir()

    try:
        module.write_csv(
            stage / "comic_audit.csv", audit, audit_fields
        )

        node_count, updated_nodes = update_nodes(
            module, run, stage, affected, updates
        )

        print("Updating raw-edge dates...", flush=True)
        edge_counts = update_edges(module, run, stage, updated_audit)

        remaining_undated = [
            row["source_file"]
            for row in audit
            if not str(row["network_year"]).strip()
        ]

        counts = summary.setdefault("counts", {})
        counts.update({
            "nodes": node_count,
            "raw_edges": edge_counts["raw_edges"],
            "raw_edges_without_year": edge_counts["raw_edges_without_year"],
            "aggregate_edges": edge_counts["aggregate_edges"],
            "annual_edges": edge_counts["annual_edges"],
            "comics_without_year": len(remaining_undated),
        })

        summary["manual_year_update"] = {
            "updated_at": datetime.now().isoformat(),
            "manual_csv": str(manual),
            "years_added": updates,
            "nodes_updated": updated_nodes,
            "raw_edges_updated": edge_counts["raw_edges_updated"],
            "manual_entries_for_review": unresolved,
            "comics_still_without_year": remaining_undated,
        }

        (stage / status_path.name).write_text(
            json.dumps(summary, indent=2),
            encoding="utf-8",
        )

        print(
            "Backing up and replacing the original files. "
            "Keep Excel closed...",
            flush=True,
        )

        backup = install(
            run,
            stage,
            [*FILES_TO_UPDATE, status_path.name],
            stamp,
        )

    except BaseException:
        print(
            f"\nUpdate did not finish. Keep any backup and marker files.\n"
            f"Staged files: {stage}"
        )
        raise

    else:
        shutil.rmtree(stage, ignore_errors=True)

    print("\nMANUAL YEARS UPDATE FINISHED")
    print(f"Comic years filled: {len(updates):,}")
    print(f"Node date summaries updated: {updated_nodes:,}")
    print(f"Raw evidence rows updated: {edge_counts['raw_edges_updated']:,}")
    print(f"Comics still without a year: {len(remaining_undated):,}")
    print(f"\nUpdated original folder:\n{run}")
    print(f"\nBackup:\n{backup}")
    print(
        f"\nThe remaining undated filenames are listed under "
        f"'manual_year_update' in {status_path.name}."
    )


if __name__ == "__main__":
    main()