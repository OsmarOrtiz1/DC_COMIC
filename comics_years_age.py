from pathlib import Path
import duckdb

#=========================
#SETTINGS 
#=========================

PROJECT = Path("/Users/26472724/OneDrive - University of Limerick/Desktop/PhD UL/DC_NETWORK")
INPUT = PROJECT / "network_dated_final"
OUTPUT = PROJECT / "network_analysis_v3.0"

CHARACTERS = INPUT / "nodes.csv"
RAW_EDGES = INPUT / "story_appearances.csv"

FIRST_YEAR = 1935
LAST_YEAR = 2026

AGES = [
    ("Platinum", 1935, 1937),
    ("Golden", 1938, 1955),
    ("Silver", 1956, 1969),
    ("Bronze", 1970, 1985),
    ("Iron", 1986, 2010),
    ("Modern", 2011, 2026)
]

OUTPUT.mkdir(parents=True, exist_ok=True)


def sql_string(value):
    """Quote a path or string safely for SQL."""
    return "'" + str(value).replace("'", "''") + "'"



database = OUTPUT / "network_comics_work.duckdb"

if database.exists():
    raise FileExistsError(
        f"{database} already exists. Use a different OUTPUT folder "
        "to avoid overwriting an earlier analysis."
    )

con = duckdb.connect(str(database))
con.execute("SET memory_limit = '2GB'")
con.execute("SET threads = 2")
con.execute(
    f"SET temp_directory = {sql_string(OUTPUT / 'temporary_files')}"
)

#==================================
# LOAD ELIGIBLE NODES 
#==================================

print("Loading character and unknown node IDs...", flush=True)

con.execute(f"""
    CREATE TABLE eligible_nodes AS
    SELECT node_id
    FROM read_csv(
        {sql_string(CHARACTERS)},
        header = true,
        all_varchar = true
    )
    WHERE entity_kind IN ('character', 'external_url', 'manual_review', 'unknown')
""")

duplicates = con.execute("""
    SELECT COUNT(*) - COUNT(DISTINCT node_id)
    FROM eligible_nodes
""").fetchone()[0]

if duplicates:
    raise ValueError("Duplicate eligible node IDs found.")

#==================================
# LOAD ELIGIBLE YEARLY EDGES 
#==================================

print("Reading comic-level edges...", flush=True)

con.execute(f"""
    CREATE TABLE pair_comic AS
    SELECT
        CAST(a.network_year AS INTEGER) AS year,
        a.source_file AS comic_id,
        a.source_file AS comic_title,
        LEAST(a.node_id, b.node_id) AS source,
        GREATEST(a.node_id, b.node_id) AS target,
        CAST(0 AS BIGINT) AS synopsis_evidence_count,
        COUNT(*) AS shared_story_count
    FROM read_csv(
        {sql_string(RAW_EDGES)},
        header = true,
        all_varchar = true
    ) a
    JOIN read_csv(
        {sql_string(RAW_EDGES)},
        header = true,
        all_varchar = true
    ) b
        ON a.source_file = b.source_file
       AND a.story = b.story
       AND a.node_id < b.node_id
    JOIN eligible_nodes s
        ON s.node_id = a.node_id
    JOIN eligible_nodes t
        ON t.node_id = b.node_id
    WHERE TRY_CAST(a.network_year AS INTEGER)
          BETWEEN {FIRST_YEAR} AND {LAST_YEAR}
    GROUP BY
        year, comic_id, comic_title, source, target
""")
# Preserve a year-level summary for the overall, age, and yearly analyses.
con.execute("""
    CREATE TABLE pair_year AS
    SELECT
        year,
        source,
        target,
        SUM(synopsis_evidence_count) AS synopsis_evidence_count,
        SUM(shared_story_count) AS shared_story_count
    FROM pair_comic
    GROUP BY year, source, target
""")

#==================================
# RESULT TABLE 
#==================================

con.execute("""
    CREATE TABLE results (
        scale VARCHAR,
        period VARCHAR,
        start_year INTEGER,
        end_year INTEGER,
        network VARCHAR,
        node_count BIGINT,
        edge_count BIGINT,
        synopsis_evidence_count BIGINT,
        shared_story_count BIGINT
    )
""")


def analyse_period(scale, period, start, end, network):
    """
    Nodes are endpoints of retained edges.
    Edges are distinct undirected pairs within the period.
    """

    if network == "coappearance":
        # Includes both synopsis-supported and coappearance-only pairs.
        condition = "shared_story_count > 0"
    elif network == "synopsis_supported":
        condition = "synopsis_evidence_count > 0"
    else:
        raise ValueError(network)

    # Filter pair-years before aggregating across a period.
    # A synopsis network includes only years with synopsis support.
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE period_edges AS
        SELECT
            source,
            target,
            SUM(synopsis_evidence_count) AS synopsis_evidence_count,
            SUM(shared_story_count) AS shared_story_count
        FROM pair_year
        WHERE year BETWEEN ? AND ?
          AND {condition}
        GROUP BY source, target
    """, [start, end])

    nodes = con.execute("""
        SELECT COUNT(*)
        FROM (
            SELECT source AS node_id FROM period_edges
            UNION
            SELECT target AS node_id FROM period_edges
        )
    """).fetchone()[0]

    edges, synopsis_count, story_count = con.execute("""
        SELECT
            COUNT(*),
            COALESCE(SUM(synopsis_evidence_count), 0),
            COALESCE(SUM(shared_story_count), 0)
        FROM period_edges
    """).fetchone()

    con.execute("""
        INSERT INTO results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        scale,
        period,
        start,
        end,
        network,
        nodes,
        edges,
        synopsis_count,
        story_count,
    ])

    print(
        f"{network:20} | {period:14} | "
        f"{nodes:>9,} nodes | {edges:>10,} edges",
        flush=True,
    )

#==================================
# RUN BOTH NETWORK DEFINITIONS 
#==================================

for network in ("coappearance",):
    analyse_period(
        "overall",
        f"{FIRST_YEAR}-{LAST_YEAR}",
        FIRST_YEAR,
        LAST_YEAR,
        network,
    )

    for age, start, end in AGES:
        analyse_period("age", age, start, end, network)

    for year in range(FIRST_YEAR, LAST_YEAR + 1):
        analyse_period("year", str(year), year, year, network)

#==================================
# CHECK NESTED NETWORK COUNTS 
#==================================

# Synopsis-supported edges are a subset of coappearance edges.
invalid = con.execute("""
    SELECT COUNT(*)
    FROM results s
    JOIN results c
      ON s.scale = c.scale
     AND s.period = c.period
    WHERE s.network = 'synopsis_supported'
      AND c.network = 'coappearance'
      AND (
          s.edge_count > c.edge_count
          OR s.node_count > c.node_count
      )
""").fetchone()[0]

if invalid:
    raise ValueError(
        "Synopsis network exceeds coappearance network. "
        "Check the input evidence definitions."
    )

#==================================
# PRINT RESULTS ONLY 
#==================================

for scale in ("overall", "age", "year"):
    print(f"\n{'=' * 85}")
    print(f"NETWORK SIZE — {scale.upper()}")
    print(f"{'=' * 85}")

    rows = con.execute("""
        SELECT
            period,
            network,
            node_count,
            edge_count
        FROM results
        WHERE scale = ?
        ORDER BY start_year, network
    """, [scale]).fetchall()

    print(
        f"{'Period':<15}"
        f"{'Network':<23}"
        f"{'Characters/unknowns':>22}"
        f"{'Connections':>18}"
    )

    for period, network, nodes, edges in rows:
        print(
            f"{period:<15}"
            f"{network:<23}"
            f"{nodes:>22,}"
            f"{edges:>18,}"
        )

con.close()
print("\nFinished.")