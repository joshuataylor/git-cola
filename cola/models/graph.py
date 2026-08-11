from __future__ import annotations
from dataclasses import dataclass
from dataclasses import field
from enum import Enum


class GraphRowColor(Enum):
    NORMAL = 0
    MERGE = 1
    HEAD = 2


@dataclass
class EdgeSegment:
    from_column: int
    to_column: int
    color_index: int


@dataclass
class GraphRow:
    commit_oid: str
    commit_column: int
    edges_to_parent: list[EdgeSegment] = field(default_factory=list)
    color: GraphRowColor = GraphRowColor.NORMAL


@dataclass
class GraphResult:
    rows: list[GraphRow]
    max_columns: int


def build_graph(
    commits: list[tuple[str, list[str]]],
    head_oid: str | None = None,
) -> GraphResult:
    """Build a row-based graph representation from a list of commits.

    Commits are received in topo order from RepoReader (oldest first).
    """
    active_lanes: list[str | None] = []
    # oid -> its column in active_lanes, and the set of columns currently held
    # by a None placeholder. Both mirror active_lanes so the per-commit lane
    # lookups below avoid an O(lanes) list.index()/`in` scan on wide histories.
    lane_index: dict[str, int] = {}
    free_slots: set[int] = set()
    color_map: dict[str, int] = {}
    next_color = 0
    rows: list[GraphRow] = []
    max_columns = 0

    all_oids = {commit_and_parents[0] for commit_and_parents in commits}

    # The graph is built top-to-bottom (newest first), so the input is reversed.
    for oid, parent_oids in reversed(commits):
        # Is this a terminal commit without any parents?
        terminal_commit = False
        # Find the commit in active_lanes or allocate a new lane.
        commit_column = lane_index.get(oid)
        if commit_column is None:
            if parent_oids and not all_oids.intersection(parent_oids):
                terminal_commit = True
                active_lanes = [None]
                lane_index = {}
                free_slots = {0}
                commit_column = 0
            else:
                commit_column = len(active_lanes)
                active_lanes.append(oid)
                lane_index[oid] = commit_column

        # Assign a color for this commit's lane.
        commit_color = color_map.get(oid, None)
        if commit_color is None:
            commit_color = next_color
            next_color += 1
        else:
            # This is the last time we see this commit, remove it from color_map to reduce
            # max memory consumption
            color_map.pop(oid)

        edges: list[EdgeSegment] = []

        # Pass through lanes
        for i, lane_oid in enumerate(active_lanes):
            if lane_oid is not None and lane_oid != oid:
                edges.append(
                    EdgeSegment(
                        from_column=i,
                        to_column=i,
                        color_index=color_map[lane_oid],
                    )
                )

        if parent_oids and not terminal_commit:
            for i, parent_oid in enumerate(parent_oids):
                # Select color if not selected: first parent gets commit color,
                # others get next color
                parent_color = color_map.get(parent_oid, None)
                if parent_color is None:
                    if i == 0:
                        parent_color = commit_color
                    else:
                        parent_color = next_color
                        next_color += 1
                    color_map[parent_oid] = parent_color

                parent_col = lane_index.get(parent_oid)
                if parent_col is not None:
                    if i == 0:
                        # First parent means commit no longer uses its column
                        active_lanes[commit_column] = None
                        del lane_index[oid]
                        free_slots.add(commit_column)
                elif i == 0:
                    # First parent takes the commit's lane.
                    active_lanes[commit_column] = parent_oid
                    del lane_index[oid]
                    lane_index[parent_oid] = commit_column
                    parent_col = commit_column
                elif free_slots:
                    # Try to reuse a None slot (the lowest, as index(None) did).
                    parent_col = min(free_slots)
                    active_lanes[parent_col] = parent_oid
                    lane_index[parent_oid] = parent_col
                    free_slots.discard(parent_col)
                else:
                    # Append new
                    parent_col = len(active_lanes)
                    active_lanes.append(parent_oid)
                    lane_index[parent_oid] = parent_col

                edges.append(
                    EdgeSegment(
                        from_column=commit_column,
                        to_column=parent_col,
                        color_index=parent_color,
                    )
                )
        else:
            # Root commit - remove its lane.
            active_lanes[commit_column] = None
            lane_index.pop(oid, None)
            free_slots.add(commit_column)

        max_columns = max(max_columns, len(active_lanes))

        # Trim trailing None slots.
        while active_lanes and active_lanes[-1] is None:
            free_slots.discard(len(active_lanes) - 1)
            active_lanes.pop()

        if head_oid is not None and oid == head_oid:
            color = GraphRowColor.HEAD
        elif len(parent_oids) > 1:
            color = GraphRowColor.MERGE
        else:
            color = GraphRowColor.NORMAL

        row = GraphRow(
            commit_oid=oid,
            commit_column=commit_column,
            edges_to_parent=edges,
            color=color,
        )
        rows.append(row)

    return GraphResult(rows=rows, max_columns=max_columns)
