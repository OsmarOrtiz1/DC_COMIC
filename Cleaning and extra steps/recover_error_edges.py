import argparse
import csv
import json
import os
import runpy
import shutil
import sqlite3
import tempfile

from collections import Counter, defaultdict
from datetime import datetime
from itertools import groupby
from pathlib import Path
from types import SimpleNamespace


csv.field_size_limit(100_000_000)

OUTPUT_FILES = [
    "characters_dated.csv",
    "raw_edges_dated.csv",
    "edges.csv",
    "edges_by_year.csv",
    "comic_audit.csv",
    "errors.csv",
]

STATUS_FILES = [
    "PARTIAL.json",
    "COMPLETE.json",
]

ERROR_FIELDS = ["source_file", "stage", "error"]

EXCLUDED_KINDS = {"association", "other"}

# Only fields needed to recalculate aggregated edges.
AGGREGATION_INPUT_FIELDS = [
    "source",
    "target",
    "source_label",
    "target_label",
    "source_universe",
    "target_universe",
    "comic_title",
    "story_title",
    "html_source_file",
    "context_label",
    "edge_type",
    "date_source",
    "network_year",
    "evidence_category",
]


# ============================================================
# CSV HELPERS
# ============================================================

def read_rows(path, required=()):
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)

        missing = set(required) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(
                f"{path.name} is missing columns: {sorted(missing)}"
            )

        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError(
                    f"Malformed CSV record in {path.name}, "
                    f"ending at line {reader.line_num}"
                )
            yield row


def split_values(value):
    return {
        part for part in str(value or "").split("|")
        if part
    }


def merge_node(existing, recovered):
    """
    Merge node summaries from disjoint sets of comics.

    Existing original observations are preserved.
    Classifications observed in both runs are retained together.
    """
    result = dict(existing)

    for field in (
        "entity_kind",
        "original_entity_kinds",
        "recovery_batches",
        "display_names",
        "categories",
        "date_sources",
    ):
        result[field] = "|".join(sorted(
            split_values(existing.get(field, ""))
            | split_values(recovered.get(field, ""))
        ))

    for field in ("appearance_count", "undated_story_count"):
        result[field] = (
            int(existing.get(field) or 0)
            + int(recovered.get(field) or 0)
        )

    years = {
        int(year)
        for year in (
            split_values(existing.get("observed_years", ""))
            | split_values(recovered.get("observed_years", ""))
        )
    }

    result["observed_years"] = "|".join(
        str(year) for year in sorted(years)
    )
    result["first_observed_year"] = min(years) if years else ""
    result["last_observed_year"] = max(years) if years else ""

    return result


# ============================================================
# FIND THE ORIGINAL RUN
# ============================================================

def valid_run(path):
    return (
        path.is_dir()
        and all((path / name).is_file() for name in OUTPUT_FILES)
        and any((path / name).is_file() for name in STATUS_FILES)
        and not (path / "INCOMPLETE.txt").exists()
        and not (path / "FAILED.txt").exists()
        and not (path / "RECOVERY_IN_PROGRESS.json").exists()
    )


def choose_run(base, requested):
    if requested is not None:
        requested = requested.resolve()
        if not valid_run(requested):
            raise ValueError(
                f"Not a finished, usable output folder:\n{requested}\n"
                "Check its output files and status markers."
            )
        return requested

    if not base.is_dir():
        raise FileNotFoundError(f"Output base does not exist: {base}")

    candidates = sorted(
        (path for path in base.iterdir() if valid_run(path)),
        key=lambda path: path.name,
    )

    if not candidates:
        raise ValueError(
            f"No finished output folders found in:\n{base}"
        )

    if len(candidates) == 1:
        print(f"Using output folder:\n{candidates[0]}\n")
        return candidates[0]

    print("Choose the ORIGINAL output folder to update:\n")
    for number, path in enumerate(candidates, 1):
        print(f"  {number}. {path.name}")

    while True:
        answer = input("\nFolder number: ").strip()
        try:
            number = int(answer)
            if 1 <= number <= len(candidates):
                return candidates[number - 1]
        except ValueError:
            pass

        print("Enter one of the listed numbers.")


# ============================================================
# LOAD ONLY THE TARGET COMICS
# ============================================================

def load_recovery_appearances(module, csv_path, targets):
    appearances = {}
    problems = {}
    counts = Counter()

    for number, row in module.read_appearance_rows(csv_path):
        source = module.file_key(row["source_file"])

        if source not in targets:
            continue

        stories = appearances.setdefault(source, {})
        kind = row["entity_kind"].strip().lower()

        # IMPORTANT: filter before identity creation and merging.
        if kind in EXCLUDED_KINDS:
            counts[f"excluded_{kind}_rows"] += 1
            continue

        if source in problems:
            continue

        try:
            if kind not in module.CHARACTER_KINDS | module.OTHER_KINDS:
                raise ValueError(f"Unexpected entity_kind: {kind!r}")

            if module.boolean_value(
                row["is_exact_duplicate"], "is_exact_duplicate"
            ):
                counts["duplicate_rows_skipped"] += 1
                continue

            if module.boolean_value(
                row["mentioned_only"], "mentioned_only"
            ):
                counts["mentioned_only_rows_skipped"] += 1
                continue

            if kind == "invalid":
                counts["invalid_rows_skipped"] += 1
                continue

            if row["listed_as"] not in module.APPEARANCE_LABELS:
                counts["nonappearance_rows_skipped"] += 1
                continue

            key = module.story_key(row["story"])
            record = module.make_entity(row, source, key)

            story = stories.setdefault(
                key,
                {
                    "title": module.story_title(row["story"]),
                    "records": {},
                },
            )

            previous = story["records"].get(record["node_id"])

            if previous is None:
                story["records"][record["node_id"]] = record
            else:
                if previous["entity_kind"] != record["entity_kind"]:
                    raise ValueError(
                        "Classification conflict remains after filtering: "
                        f"{record['node_id']}"
                    )

                for field in (
                    "original_entity_kinds",
                    "recovery_batches",
                ):
                    previous[field].update(record[field])

            counts["eligible_recovery_rows"] += 1

        except ValueError as error:
            problems[source] = f"CSV record {number}: {error}"
            appearances[source] = {}

    for source in targets - set(appearances):
        problems[source] = "No matching observations found in the CSV."

    return appearances, problems, counts


# ============================================================
# RECOVER COMICS
# ============================================================

def recover_comics(module, targets, original_audit):
    csv_path = Path(module.APPEARANCES_CSV)
    original_html = Path(module.ORIGINAL_HTML_FOLDER)
    repaired_html = Path(module.REPAIRED_HTML_FOLDER)

    if not csv_path.is_file():
        raise FileNotFoundError(csv_path)

    for root in (original_html, repaired_html):
        if not root.is_dir():
            raise FileNotFoundError(root)

    print(f"Loading recovery observations from:\n{csv_path}", flush=True)

    appearances, problems, counts = load_recovery_appearances(
        module, csv_path, targets
    )

    resolve_html = module.build_html_resolver(
        original_html, repaired_html
    )

    choices = {}
    physical_sources = defaultdict(list)

    original_paths = {
        str(Path(row["html_path_used"]).resolve())
        for row in original_audit
        if row.get("html_path_used")
    }

    for source in sorted(targets):
        if source in problems:
            continue

        try:
            path, origin = resolve_html(source)
            physical = str(path.resolve())

            if physical in original_paths:
                raise ValueError(
                    "This HTML file is already represented in the "
                    "original comic audit under an existing source."
                )

            choices[source] = (path, origin)
            physical_sources[physical].append(source)

        except ValueError as error:
            problems[source] = str(error)

    for sources in physical_sources.values():
        if len(sources) > 1:
            for source in sources:
                problems[source] = (
                    "Multiple recovery filenames resolve to the same HTML: "
                    + repr(sources)
                )
                choices.pop(source, None)

    recovered_nodes = {}
    recovered_edges = []
    recovered_audit = []
    successful = set()

    for source in module.tqdm(
        sorted(choices), desc="Recovering classification errors"
    ):
        path, origin = choices[source]
        stories = appearances[source]

        try:
            metadata, synopses = module.read_comic(path, source)

            local_registry = {}
            local_edges = []
            unmatched = 0

            for key, story in stories.items():
                synopsis = synopses.get(
                    key,
                    {
                        **metadata,
                        "title": story["title"],
                        "text": "",
                    },
                )

                if key not in synopses:
                    unmatched += 1

                records = list(story["records"].values())

                for record in records:
                    module.add_node(local_registry, record, synopsis)

                local_edges.extend(
                    module.complete_story_edges(synopsis, records)
                )

            local_nodes = list(
                module.character_rows(local_registry)
            )

        except (ValueError, OSError, UnicodeError) as error:
            problems[source] = (
                f"{type(error).__name__}: {error}"
            )
            continue

        # Only commit a comic after all its processing succeeds.
        for node in local_nodes:
            node_id = node["node_id"]

            if node_id in recovered_nodes:
                recovered_nodes[node_id] = merge_node(
                    recovered_nodes[node_id], node
                )
            else:
                recovered_nodes[node_id] = node

        recovered_edges.extend(local_edges)
        successful.add(source)

        counts["recovered_stories"] += len(stories)
        counts["stories_without_matching_synopsis"] += unmatched

        recovered_audit.append({
            "source_file": source,
            "html_path_used": str(path),
            "html_origin": origin,
            "stories": len(stories),
            **{
                field: metadata[field]
                for field in module.DATE_FIELDS
            },
        })

    counts["recovered_comics"] = len(successful)
    counts["recovered_raw_edges"] = len(recovered_edges)

    return (
        successful,
        recovered_nodes,
        recovered_edges,
        recovered_audit,
        problems,
        counts,
    )


# ============================================================
# REBUILD EDGE TOTALS WITHOUT LOADING ALL ORIGINAL EDGES IN RAM
# ============================================================

def aggregate_from_database(module, database, by_year):
    cursor = database.execute(
        "SELECT payload FROM evidence "
        "ORDER BY source, target, year"
    )

    decoded = (json.loads(row[0]) for row in cursor)

    if by_year:
        key = lambda edge: (
            edge["source"],
            edge["target"],
            edge["network_year"],
        )
    else:
        key = lambda edge: (
            edge["source"],
            edge["target"],
        )

    for _, group in groupby(decoded, key):
        yield module.aggregate_pair(group, by_year)


def rebuild_edges(module, run, stage, recovered_edges, successful):
    totals = Counter()

    # The temporary database is only for merging/aggregation.
    # No original comics are parsed again.
    with tempfile.TemporaryDirectory(
        prefix="dc_network_recovery_"
    ) as temporary:
        database = sqlite3.connect(
            Path(temporary) / "aggregation.sqlite"
        )

        try:
            database.execute(
                """
                CREATE TABLE evidence (
                    source TEXT,
                    target TEXT,
                    year INTEGER,
                    payload TEXT
                )
                """
            )

            database.execute("PRAGMA cache_size = -65536")

            batch = []

            with (stage / "raw_edges_dated.csv").open(
                "w", encoding="utf-8", newline=""
            ) as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=module.RAW_FIELDS,
                )
                writer.writeheader()

                inputs = [
                    (
                        True,
                        read_rows(
                            run / "raw_edges_dated.csv",
                            module.RAW_FIELDS,
                        ),
                    ),
                    (False, recovered_edges),
                ]

                for is_original, rows in inputs:
                    for row in rows:
                        source_file = module.file_key(
                            row["html_source_file"]
                        )

                        # Additional protection against double recovery.
                        if is_original and source_file in successful:
                            raise ValueError(
                                "Recovery stopped to avoid duplicates: "
                                f"{source_file} already has original raw edges."
                            )

                        writer.writerow(row)

                        payload = {
                            field: row[field]
                            for field in AGGREGATION_INPUT_FIELDS
                        }

                        year_text = str(
                            payload["network_year"]
                        ).strip()

                        year = int(year_text) if year_text else None
                        payload["network_year"] = (
                            year if year is not None else ""
                        )

                        batch.append((
                            payload["source"],
                            payload["target"],
                            year,
                            json.dumps(payload),
                        ))

                        totals["raw_edges"] += 1
                        totals[row["evidence_category"]] += 1

                        if year is None:
                            totals["raw_edges_without_year"] += 1

                        if len(batch) >= 10_000:
                            database.executemany(
                                "INSERT INTO evidence VALUES (?, ?, ?, ?)",
                                batch,
                            )
                            database.commit()
                            batch.clear()

                        if totals["raw_edges"] % 250_000 == 0:
                            print(
                                f"  Merged {totals['raw_edges']:,} evidence rows",
                                flush=True,
                            )

            if batch:
                database.executemany(
                    "INSERT INTO evidence VALUES (?, ?, ?, ?)",
                    batch,
                )
                database.commit()

            print("Building combined edge summaries...", flush=True)

            database.execute(
                "CREATE INDEX evidence_pairs "
                "ON evidence(source, target, year)"
            )
            database.commit()

            totals["aggregate_edges"] = module.write_csv(
                stage / "edges.csv",
                aggregate_from_database(module, database, False),
                module.AGGREGATE_FIELDS,
            )

            totals["annual_edges"] = module.write_csv(
                stage / "edges_by_year.csv",
                aggregate_from_database(module, database, True),
                ["network_year", *module.AGGREGATE_FIELDS],
            )

        finally:
            database.close()

    return totals


# ============================================================
# BACKUP AND INSTALL THE MERGED OUTPUTS
# ============================================================

def install_outputs(run, stage, stamp):
    names = [
        *OUTPUT_FILES,
        *STATUS_FILES,
        "recovery_report.json",
    ]

    backup = run / f"backup_before_recovery_{stamp}"
    backup.mkdir()

    existing = []

    # Finish the backup before replacing any original output.
    for name in names:
        original = run / name
        if original.is_file():
            shutil.copy2(original, backup / name)
            existing.append(name)

    marker = run / "RECOVERY_IN_PROGRESS.json"
    marker.write_text(
        json.dumps({
            "backup": str(backup),
            "stage": str(stage),
            "files": names,
            "originally_present": existing,
        }, indent=2),
        encoding="utf-8",
    )

    try:
        for name in names:
            replacement = stage / name
            destination = run / name

            if replacement.is_file():
                os.replace(replacement, destination)
            elif name in STATUS_FILES and destination.exists():
                # Remove the superseded COMPLETE/PARTIAL marker.
                destination.unlink()

    except BaseException:
        # Restore the old run if publication fails or is interrupted.
        # A hard shutdown may leave the marker and backup for recovery.
        for name in names:
            destination = run / name

            if name in existing:
                shutil.copy2(backup / name, destination)
            elif destination.exists():
                destination.unlink()

        marker.unlink()
        raise

    else:
        marker.unlink()

    return backup


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Recover classification failures and merge the outputs."
    )

    parser.add_argument(
        "--script",
        type=Path,
        default=Path(
            r"C:\Users\26472724\OneDrive - University of Limerick\Desktop\PhD UL\DC_NETWORK\edge_finder.py"
        ),
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Original run folder. Otherwise choose from OUTPUT_BASE.",
    )

    args = parser.parse_args()

    if args.script is None:
        script_candidates = [
            Path(__file__).resolve().with_name("edge_finder.py"),
            Path(__file__).resolve().with_name("edge_finder.py"),
        ]
        script_path = next(
            (candidate for candidate in script_candidates if candidate.is_file()),
            None,
        )
    else:
        script_path = args.script.resolve()

    if script_path is None:
        raise FileNotFoundError(
            "Original script not found. Place this recovery script beside "
            "edge_finder.py or edge_finder.py, or provide --script."
        )

    if script_path == Path(__file__).resolve():
        raise ValueError(
            "Save this recovery script as recover_error_edges.py or "
            "recover_classification.py, not as the original edge_finder.py."
        )

    if not script_path.is_file():
        raise FileNotFoundError(
            f"Original script not found: {script_path}\n"
            "Place this recovery script beside edge_finder.py or edge_finder.py, "
            "or provide --script."
        )

    # Load functions/settings without running the original main().
    module = SimpleNamespace(**runpy.run_path(
        str(script_path),
        run_name="dc_network_recovery_import",
    ))

    run = choose_run(
        Path(module.OUTPUT_BASE),
        args.output,
    )

    audit_fields = [
        "source_file",
        "html_path_used",
        "html_origin",
        "stories",
        *module.DATE_FIELDS,
    ]

    errors = list(read_rows(run / "errors.csv", ERROR_FIELDS))
    original_audit = list(
        read_rows(run / "comic_audit.csv", audit_fields)
    )

    processed_sources = {
        module.file_key(row["source_file"])
        for row in original_audit
    }

    targets = {
        module.file_key(row["source_file"])
        for row in errors
        if "Conflicting classifications" in row["error"]
        and not module.is_excluded(row["source_file"])
    }

    already_processed = targets & processed_sources
    targets -= processed_sources

    if already_processed:
        print(
            f"Skipping {len(already_processed)} target comics already "
            "present in the original audit."
        )

    if not targets:
        print("No unprocessed classification-error comics remain.")
        return

    print(f"\nOriginal run: {run}")
    print(f"Classification-error comics to attempt: {len(targets):,}")
    print("Recovery-only exclusions: association, other\n")

    (
        successful,
        recovered_nodes,
        recovered_edges,
        recovered_audit,
        problems,
        recovery_counts,
    ) = recover_comics(module, targets, original_audit)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")

    report = {
        "timestamp": datetime.now().isoformat(),
        "original_run": str(run),
        "appearance_csv": str(module.APPEARANCES_CSV),
        "excluded_entity_kinds": sorted(EXCLUDED_KINDS),
        "attempted_comics": len(targets),
        "recovered_comics": sorted(successful),
        "unresolved": problems,
        "counts": dict(recovery_counts),
    }

    if not successful:
        report_path = run / f"recovery_attempt_{stamp}.json"
        report_path.write_text(
            json.dumps(report, indent=2),
            encoding="utf-8",
        )
        print("No comics were recovered. Original outputs are unchanged.")
        print(f"Attempt report: {report_path}")
        return

    print(
        f"\nRecovered {len(successful):,} comics. "
        "Preparing merged outputs...",
        flush=True,
    )

    # Keep unresolved original errors, including duplicate-title errors.
    remaining_errors = [
        row for row in errors
        if module.file_key(row["source_file"]) not in successful
    ]

    known_errors = {
        (row["source_file"], row["stage"], row["error"])
        for row in remaining_errors
    }

    for source, message in sorted(problems.items()):
        entry = (source, "recovery", message)
        if entry not in known_errors:
            remaining_errors.append({
                "source_file": source,
                "stage": "recovery",
                "error": message,
            })

    # Build everything separately before replacing original outputs.
    stage = run / f"_recovery_stage_{stamp}"
    stage.mkdir()

    try:
        nodes = {}

        for row in read_rows(
            run / "characters_dated.csv",
            module.NODE_FIELDS,
        ):
            node_id = row["node_id"]

            if node_id in nodes:
                raise ValueError(
                    f"Original node file contains duplicate ID: {node_id}"
                )

            nodes[node_id] = row

        added_nodes = 0

        for node_id, recovered in recovered_nodes.items():
            if node_id in nodes:
                nodes[node_id] = merge_node(nodes[node_id], recovered)
            else:
                nodes[node_id] = recovered
                added_nodes += 1

        node_count = module.write_csv(
            stage / "characters_dated.csv",
            (nodes[node_id] for node_id in sorted(nodes)),
            module.NODE_FIELDS,
        )
        nodes.clear()

        combined_audit = original_audit + recovered_audit

        module.write_csv(
            stage / "comic_audit.csv",
            combined_audit,
            audit_fields,
        )

        module.write_csv(
            stage / "errors.csv",
            remaining_errors,
            ERROR_FIELDS,
        )

        edge_totals = rebuild_edges(
            module,
            run,
            stage,
            recovered_edges,
            successful,
        )

        totals = {
            **dict(edge_totals),
            "nodes": node_count,
            "comic_files": len(combined_audit),
            "stories": sum(
                int(row["stories"] or 0)
                for row in combined_audit
            ),
            "comics_without_year": sum(
                str(row["network_year"]).strip() == ""
                for row in combined_audit
            ),
            "repaired_copies_used": sum(
                row["html_origin"] == "repaired"
                for row in combined_audit
            ),
            "failed_comics": len({
                row["source_file"]
                for row in remaining_errors
            }),
        }

        report["new_node_ids_added"] = added_nodes
        report["combined_totals"] = totals

        (stage / "recovery_report.json").write_text(
            json.dumps(report, indent=2),
            encoding="utf-8",
        )

        old_status = next(
            run / name
            for name in STATUS_FILES
            if (run / name).is_file()
        )

        summary = json.loads(old_status.read_text(encoding="utf-8"))

        summary["status"] = (
            "partial" if remaining_errors else "complete"
        )
        summary["updated_at"] = datetime.now().isoformat()
        summary["counts"] = totals
        summary["last_recovery"] = {
            "timestamp": report["timestamp"],
            "recovered_comics": len(successful),
            "new_node_ids_added": added_nodes,
            "excluded_entity_kinds": sorted(EXCLUDED_KINDS),
            "report": "recovery_report.json",
        }

        notes = summary.setdefault("notes", [])
        recovery_note = (
            "Classification recovery excludes association and other rows "
            "only in recovered comics. Existing original observations "
            "are preserved. Node entity_kind can contain multiple "
            "observed kinds separated by |."
        )

        if recovery_note not in notes:
            notes.append(recovery_note)

        status_name = (
            "PARTIAL.json" if remaining_errors else "COMPLETE.json"
        )

        (stage / status_name).write_text(
            json.dumps(summary, indent=2),
            encoding="utf-8",
        )

        print("Backing up and updating the original output files...", flush=True)

        backup = install_outputs(run, stage, stamp)

    except BaseException:
        print(
            f"\nRecovery did not finish. Staged files, if any, are in:\n"
            f"{stage}"
        )
        raise

    else:
        shutil.rmtree(stage, ignore_errors=True)

    print("\nRECOVERY FINISHED")
    print(f"Comics recovered: {len(successful):,}")
    print(f"New node IDs added: {added_nodes:,}")
    print(f"Recovered evidence rows: {len(recovered_edges):,}")
    print(f"Comics still listed in errors.csv: {totals['failed_comics']:,}")
    print(f"\nUpdated outputs:\n{run}")
    print(f"\nOriginal-output backup:\n{backup}")
    print(f"\nRecovery details:\n{run / 'recovery_report.json'}")


if __name__ == "__main__":
    main()