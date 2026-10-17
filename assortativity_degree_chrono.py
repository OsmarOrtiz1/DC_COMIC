from pathlib import Path
import math
import duckdb
import networkx as nx
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import csv
import scipy as sp

#=================================
#Settings
#=================================

DATABASE = Path(
    "C:\\Users\\26472724\\OneDrive - University of Limerick\\Desktop\\PhD UL\\DC_NETWORK\\network_analysis_v3.0\\network_comics_work.duckdb"
)

CHARACTERS = Path(
    "C:\\Users\\26472724\\OneDrive - University of Limerick\\Desktop\\PhD UL\\DC_NETWORK\\network_dated_final\\nodes.csv"
)

with CHARACTERS.open(encoding="utf-8") as file:
    NODE_LABELS = {
        row["node_id"]: row["node_label"]
        for row in csv.DictReader(file)
    }

AGES = {
    "Platinum": (1935, 1937),
    "Golden": (1938, 1955),
    "Silver": (1956, 1969),
    "Bronze": (1970, 1985),
    "Iron": (1986, 2010),
    "Modern": (2011, 2026),
}

AGE = "Platinum"    #change this to the age we want to analyze
NETWORK = "coappearance" #we can change this to "synopsis_supported" 

MAX_DRAW_NODES = 2000 #maximum number of nodes to draw in the network visualization

START_YEAR, END_YEAR = AGES[AGE]

#adding this in case we want to use weights at some point 
WEIGHT_COLUMNS = {
    "coappearance": "shared_story_count",
    "synopsis_supported": "synopsis_evidence_count"
}

if NETWORK not in WEIGHT_COLUMNS:
    raise ValueError(f"Invalid network setting.")

if not DATABASE.exists():
    raise FileNotFoundError(DATABASE)

weight_column = WEIGHT_COLUMNS[NETWORK]

#==============================
#Assortativity analysis
#==============================

def endpoint_statistics(graph, weight=None):
#==================================
# weight=None: unweighted assortativity
# weight=weight_column: weighted assortativity
#==================================   
    edge_count = graph.number_of_edges()

    if edge_count == 0:
        return math.nan, math.nan, math.nan

    degrees = dict(graph.degree())
    values = dict(graph.degree(weight=weight))


    mean = math.fsum(
        degrees[node] * values[node]
        for node in graph
    ) / (2 * edge_count)

    variance = math.fsum(
        degrees[node] * (values[node] - mean) ** 2
        for node in graph
     )/ (2 * edge_count)

    if variance == 0:
        return mean, variance, math.nan

    covariance = math.fsum(
        (values[source] - mean) * (values[target] - mean)
        for source, target in graph.edges()
    ) / edge_count

    r = covariance / variance

    return mean, variance, r

def format_number(value):
    return f"{value: .4f}" if math.isfinite(value) else "undefined"

#==============================
# Checkpoints
#==============================

G = nx.Graph()
results = []

def record_checkpoint(year, label=""):
    mean, variance, degree_r = endpoint_statistics(G)
    _, _, strenght_r = endpoint_statistics(G, weight=weight_column)

    row = {
        "year": year,
        "nodes": G.number_of_nodes(),
        "edges": G.number_of_edges(),
        "degree_r": degree_r,
        "strength_r": strenght_r,
        "mean_endpoint_degree": mean,
        "endpoint_variance": variance,
    }

    results.append(row)

    print(
        f"{year} | "
        f"Nodes: {row['nodes']:>7,} | "
        f"Edges: {row['edges']:>9,} | "
        f"Degree r: {format_number(degree_r):>10} | "
        f"Strength r: {format_number(strenght_r):>10} | "
        f"{label}",
        flush=True,
    )

def add_evidence(source, target, weight):
    """Accumulate evidence without creating duplicate edges."""
    if G.has_edge(source, target):
        G[source][target]["weight"] += weight
    else:
        G.add_edge(source, target, weight=weight)

#=============================
# Build chronological network
#=============================

print(
    f"\n{AGE}: {START_YEAR}-{END_YEAR} | {NETWORK}\n"
    "checkpoint after every comic. \n"
    "Comics with the same publication year are ordered by source file name.\n",
    flush=True,
)

con = duckdb.connect(DATABASE, read_only=True)
con.execute("SET memory_limit = '1GB'")
con.execute("SET threads = 2")

try:
    comics = con.execute("""
        SELECT DISTINCT
            year,
            comic_id,
            comic_title
        FROM pair_comic
        WHERE year BETWEEN ? AND ?
        ORDER BY year, comic_id
        """,
        (START_YEAR, END_YEAR),
    ).fetchall()

    for year, comic_id, comic_title in comics:
        comic_graph = nx.Graph()

        rows = con.execute(
            f"""
            SELECT
                source,
                target,
                {weight_column} AS weight
            FROM pair_comic
            WHERE year = ?
              AND comic_id = ?
              AND {weight_column} > 0
              AND source <> target
            ORDER BY source, target
            """,
            [year, comic_id],
        ).fetchall()

        comic_graph.add_weighted_edges_from(rows)

        # Add new characters appearing for the first time
        for node in comic_graph.nodes:
            if node not in G:
                G.add_node(
                    node,
                    first_year_in_age=year,
                    first_comic_in_age=comic_id,
                )

        # Add edges from comic
        for source, target, data in comic_graph.edges(data=True):
            add_evidence(source, target, data["weight"])

        record_checkpoint(
            year,
            label=f"| {comic_title} | {comic_id}",
        )
finally:
    con.close()

if G.number_of_edges() == 0:
    raise ValueError("No edges found")

#=============================
# Plots
#=============================

node_counts = [row["nodes"] for row in results]

fig, ax = plt.subplots(figsize=(10, 6))

ax.plot(
    node_counts,
    [row["degree_r"] for row in results],
    marker=".",
    markersize=4,
    label="Degree Assortativity unweighted",
)
# in case we want to plot it
#ax.plot(
#    node_counts,
#    [row["strength_r"] for row in results],
#    marker=".",
#    markersize=4,
#    label="Strength Assortativity weighted",
#)

ax.axhline(0, color="grey", linestyle="--", linewidth=1)

ax.set_xlabel("Characters in the Network")
ax.set_ylabel("Assortativity")
ax.set_title(
    f"{AGE}: {START_YEAR} - {END_YEAR} - {NETWORK}\n"
    "Comics-by-comic network growth"
)

ax.legend()
ax.grid(alpha=0.2)

fig.tight_layout()

# Comic by comic assortativity

years = [row["year"] for row in results]
degree_assortativity = [row["degree_r"] for row in results]

fig, ax = plt.subplots(figsize=(11, 6))

ax.plot(
    years,
    degree_assortativity,
    marker="o",
    markersize=5,
    markevery=1,
    linewidth=0.8,
    label="Degree assortativity",
)

ax.axhline(0, color="grey", linestyle="--", linewidth=1)

ax.set_xlabel("Publication year")
ax.set_ylabel("Assortativity")
ax.set_title(
    f"{AGE}: {START_YEAR} - {END_YEAR} - {NETWORK.replace('_', ' ').title()}\n"
    "Comic-by-comic network growth"
)

ax.legend()
ax.grid(alpha=0.2)
fig.tight_layout()

# Final network

# using GC suggested by PM

GC = G.subgraph(max(nx.connected_components(G), key=len)).copy()

degrees = dict(GC.degree())
strengths = dict(GC.degree(weight="weight"))

selected = sorted(GC, key=lambda node: (-degrees[node], node))[:MAX_DRAW_NODES]
H = GC.subgraph(selected).copy()

print("\nDrawing the final network...", flush=True)

positions = nx.spring_layout(H, seed=42, weight=None)

LABEL_COUNT = 25

labelled_nodes = sorted(
    H,
    key=lambda node: (-H.degree(node), node),
)[:LABEL_COUNT]

labels = {
    node: NODE_LABELS.get(node, node)
    for node in labelled_nodes
}

fig2, axes = plt.subplots(1, 2, figsize=(19, 6))

nx.draw(
    H,
    pos=positions,
    ax=axes[0],
    with_labels=False,
    node_size=15,
    node_color="steelblue",
    edge_color="grey",
    width=0.4,
    alpha=0.6,
)

nx.draw_networkx_labels(
    H,
    pos=positions,
    ax=axes[0],
    labels=labels,
    font_size=5,
    font_color="black",
    bbox={
        "facecolor": "white",
        "edgecolor": "none",
        "alpha": 0.65,
        "pad": 0.15,
    },
)

axes[0].set_title(
    f"Final network: {len(H):,} nodes shown\n"
    "Drawing GC only"
)

axes[0].set_axis_off()

degree = np.array(list(degrees.values()))

k, n_k = np.unique(degree, return_counts=True)
P = np.cumsum(n_k[::-1])[::-1] / degree.size

axes[1].plot(k, P, "o", color="steelblue", markersize=4)
axes[1].set_yscale("log")
axes[1].set_xlabel("Degree: distinct neighbours")
axes[1].set_ylabel("CCDF P(K >= k)")
axes[1].set_title("Degree distribution, GC")
axes[1].grid(alpha=0.2)

fig2.suptitle(f"{NETWORK} - {AGE} - ({START_YEAR} - {END_YEAR})")
fig2.tight_layout()

# Quick check for errors top 20 nodes

print("\n20 most connected nodes")
print(f"{'Rank':<6} {'Degree':<10} Node")

top_nodes = sorted(
    degrees.items(),
    key=lambda item: (-item[1], item[0]),
)[:20]

for rank, (node, degree) in enumerate(top_nodes, start=1):
    print(f"{rank:<6} {degree:<10} {node}")

plt.show()
