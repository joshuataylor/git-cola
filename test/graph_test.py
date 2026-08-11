from __future__ import annotations

from cola.models.graph import GraphResult
from cola.models.graph import GraphRowColor
from cola.models.graph import build_graph


def assert_colors(result: GraphResult, expected: list[GraphRowColor]) -> None:
    actual = [r.color for r in result.rows]
    assert actual == expected


def assert_rows(result: GraphResult, expected: list[tuple[str, int]]) -> None:
    actual = [(r.commit_oid, r.commit_column) for r in result.rows]
    assert actual == expected


def assert_edges(result: GraphResult, expected: list[list[tuple[int, int]]]) -> None:
    actual = [
        [(e.from_column, e.to_column) for e in r.edges_to_parent] for r in result.rows
    ]
    assert actual == expected


def test_empty_input():
    result = build_graph([])
    assert result.rows == []
    assert result.max_columns == 0


def test_single_commit():
    result = build_graph([('aaa', [])])
    assert_rows(result, [('aaa', 0)])
    assert_edges(result, [[]])
    assert result.max_columns == 1


def test_linear_chain():
    # C-B-A
    commits = [
        ('A', []),
        ('B', ['A']),
        ('C', ['B']),
    ]
    result = build_graph(commits)
    assert_rows(result, [('C', 0), ('B', 0), ('A', 0)])
    assert_edges(result, [[(0, 0)], [(0, 0)], []])
    assert result.max_columns == 1
    colors = [r.edges_to_parent[0].color_index for r in result.rows[:-1]]
    assert colors[0] == colors[1]


def test_two_way_fork():
    # C B
    # |/
    # A
    commits = [
        ('A', []),
        ('B', ['A']),
        ('C', ['A']),
    ]
    result = build_graph(commits)
    # C
    #  B
    # A
    assert_rows(result, [('C', 0), ('B', 1), ('A', 0)])
    assert_edges(result, [[(0, 0)], [(0, 0), (1, 0)], []])


def test_three_way_fork():
    # D C B
    # |/╱
    # A
    commits = [
        ('A', []),
        ('B', ['A']),
        ('C', ['A']),
        ('D', ['A']),
    ]
    result = build_graph(commits)
    # D
    #  C
    #  B
    # A
    assert_rows(result, [('D', 0), ('C', 1), ('B', 1), ('A', 0)])
    assert_edges(result, [[(0, 0)], [(0, 0), (1, 0)], [(0, 0), (1, 0)], []])


def test_two_way_merge():
    # C
    # |\
    # A B
    commits = [
        ('A', []),
        ('B', []),
        ('C', ['A', 'B']),
    ]
    result = build_graph(commits)
    # C
    #  B
    # A
    assert_rows(result, [('C', 0), ('B', 1), ('A', 0)])
    assert_edges(result, [[(0, 0), (0, 1)], [(0, 0)], []])
    assert result.max_columns == 2
    edges = result.rows[0].edges_to_parent
    assert edges[0].color_index != edges[1].color_index
    assert_colors(
        result, [GraphRowColor.MERGE, GraphRowColor.NORMAL, GraphRowColor.NORMAL]
    )


def test_three_way_merge():
    # D
    # |\╲
    # A B C
    commits = [
        ('A', []),
        ('B', []),
        ('C', []),
        ('D', ['A', 'B', 'C']),
    ]
    result = build_graph(commits)
    # D
    #   C
    #  B
    # A
    assert_rows(result, [('D', 0), ('C', 2), ('B', 1), ('A', 0)])
    assert_edges(
        result,
        [
            [(0, 0), (0, 1), (0, 2)],
            [(0, 0), (1, 1)],
            [(0, 0)],
            [],
        ],
    )
    assert result.max_columns == 3
    assert_colors(
        result,
        [
            GraphRowColor.MERGE,
            GraphRowColor.NORMAL,
            GraphRowColor.NORMAL,
            GraphRowColor.NORMAL,
        ],
    )


def test_fork_and_merge_diamond():
    # D
    # |\
    # B C
    # |/
    # A
    commits = [
        ('A', []),
        ('B', ['A']),
        ('C', ['A']),
        ('D', ['B', 'C']),
    ]
    result = build_graph(commits)
    # D
    #  C
    # B
    #  A
    assert_rows(result, [('D', 0), ('C', 1), ('B', 0), ('A', 1)])
    assert_edges(
        result,
        [
            [(0, 0), (0, 1)],
            [(0, 0), (1, 1)],
            [(1, 1), (0, 1)],
            [],
        ],
    )
    assert result.max_columns == 2
    assert_colors(
        result,
        [
            GraphRowColor.MERGE,
            GraphRowColor.NORMAL,
            GraphRowColor.NORMAL,
            GraphRowColor.NORMAL,
        ],
    )


def test_branch_and_merge_back():
    # F
    # |
    # E
    # |\
    # C D
    # |/
    # B
    # |
    # A
    commits = [
        ('A', []),
        ('B', ['A']),
        ('C', ['B']),
        ('D', ['B']),
        ('E', ['C', 'D']),
        ('F', ['E']),
    ]
    result = build_graph(commits)
    # F
    # E
    #  D
    # C
    #  B
    #  A
    assert_rows(result, [('F', 0), ('E', 0), ('D', 1), ('C', 0), ('B', 1), ('A', 1)])
    assert_edges(
        result,
        [
            [(0, 0)],
            [(0, 0), (0, 1)],
            [(0, 0), (1, 1)],
            [(1, 1), (0, 1)],
            [(1, 1)],
            [],
        ],
    )
    assert result.max_columns == 2
    assert_colors(
        result,
        [
            GraphRowColor.NORMAL,
            GraphRowColor.MERGE,
            GraphRowColor.NORMAL,
            GraphRowColor.NORMAL,
            GraphRowColor.NORMAL,
            GraphRowColor.NORMAL,
        ],
    )


def test_two_independent_histories():
    # D-C  B-A
    commits = [
        ('A', []),
        ('B', ['A']),
        ('C', []),
        ('D', ['C']),
    ]
    result = build_graph(commits)
    assert_rows(result, [('D', 0), ('C', 0), ('B', 0), ('A', 0)])
    assert_edges(result, [[(0, 0)], [], [(0, 0)], []])
    assert result.max_columns == 1


def test_ten_commit_chain():
    # J-I-H-G-F-E-D-C-B-A
    oids = [chr(ord('A') + i) for i in range(10)]
    commits: list[tuple[str, list[str]]] = [(oids[0], [])]
    for i in range(1, 10):
        commits.append((oids[i], [oids[i - 1]]))
    result = build_graph(commits)
    assert_rows(result, [(oid, 0) for oid in reversed(oids)])
    assert_edges(result, [[(0, 0)]] * 9 + [[]])
    assert result.max_columns == 1


def test_three_way_merge_then_continue():
    #   E
    #   |
    #   D
    #  /|\
    # A B C
    commits = [
        ('A', []),
        ('B', []),
        ('C', []),
        ('D', ['A', 'B', 'C']),
        ('E', ['D']),
    ]
    result = build_graph(commits)
    # E
    # D
    #   C
    #  B
    # A
    assert_rows(result, [('E', 0), ('D', 0), ('C', 2), ('B', 1), ('A', 0)])
    assert_edges(
        result,
        [
            [(0, 0)],
            [(0, 0), (0, 1), (0, 2)],
            [(0, 0), (1, 1)],
            [(0, 0)],
            [],
        ],
    )
    assert result.max_columns == 3
    assert_colors(
        result,
        [
            GraphRowColor.NORMAL,
            GraphRowColor.MERGE,
            GraphRowColor.NORMAL,
            GraphRowColor.NORMAL,
            GraphRowColor.NORMAL,
        ],
    )


def test_nested_merge_with_shared_ancestor():
    #   F
    #  / \
    # D   E
    # |\  |
    # B C |
    # |/ /
    # A-+
    commits = [
        ('A', []),
        ('B', ['A']),
        ('C', ['A']),
        ('D', ['B', 'C']),
        ('E', ['A']),
        ('F', ['D', 'E']),
    ]
    result = build_graph(commits)
    # F
    #  E
    # D
    #   C
    # B
    #  A
    assert_rows(result, [('F', 0), ('E', 1), ('D', 0), ('C', 2), ('B', 0), ('A', 1)])
    assert_edges(
        result,
        [
            [(0, 0), (0, 1)],
            [(0, 0), (1, 1)],
            [(1, 1), (0, 0), (0, 2)],
            [(0, 0), (1, 1), (2, 1)],
            [(1, 1), (0, 1)],
            [],
        ],
    )
    assert_colors(
        result,
        [
            GraphRowColor.MERGE,
            GraphRowColor.NORMAL,
            GraphRowColor.MERGE,
            GraphRowColor.NORMAL,
            GraphRowColor.NORMAL,
            GraphRowColor.NORMAL,
        ],
    )


def test_shifted_double_diamond():
    # F
    # |\
    # D E
    # |\|
    # C B
    # |/
    # A
    commits = [
        ('A', []),
        ('B', ['A']),
        ('C', ['A']),
        ('D', ['B', 'C']),
        ('E', ['B']),
        ('F', ['D', 'E']),
    ]
    result = build_graph(commits)
    """
    F
     E
    D
    C
     B
    A
    """
    assert_rows(result, [('F', 0), ('E', 1), ('D', 0), ('C', 0), ('B', 1), ('A', 0)])
    assert_edges(
        result,
        [
            [(0, 0), (0, 1)],
            [(0, 0), (1, 1)],
            [(1, 1), (0, 1), (0, 0)],
            [(1, 1), (0, 0)],
            [(0, 0), (1, 0)],
            [],
        ],
    )
    assert_colors(
        result,
        [
            GraphRowColor.MERGE,
            GraphRowColor.NORMAL,
            GraphRowColor.MERGE,
            GraphRowColor.NORMAL,
            GraphRowColor.NORMAL,
            GraphRowColor.NORMAL,
        ],
    )


def test_head_color():
    # C
    # |
    # B (head)
    # |
    # A
    commits = [
        ('C', []),
        ('B', ['C']),
        ('A', ['B']),
    ]
    result = build_graph(commits, head_oid='B')
    assert_rows(result, [('A', 0), ('B', 0), ('C', 0)])
    assert_colors(
        result,
        [GraphRowColor.NORMAL, GraphRowColor.HEAD, GraphRowColor.NORMAL],
    )


def test_head_color_overrides_merge():
    # C (head)
    # |\
    # A B
    commits = [
        ('A', []),
        ('B', []),
        ('C', ['A', 'B']),
    ]
    result = build_graph(commits, head_oid='C')
    assert_rows(result, [('C', 0), ('B', 1), ('A', 0)])
    assert_colors(
        result,
        [GraphRowColor.HEAD, GraphRowColor.NORMAL, GraphRowColor.NORMAL],
    )


# A copy of the pre-optimisation algorithm, kept as an oracle so the
# lane-index/free-slot rewrite in build_graph can be checked for exact
# equivalence across many random DAGs.
def _reference_graph(commits, head_oid=None):
    active_lanes = []
    color_map = {}
    next_color = 0
    rows = []
    max_columns = 0
    all_oids = {c[0] for c in commits}
    for oid, parent_oids in reversed(commits):
        terminal_commit = False
        try:
            commit_column = active_lanes.index(oid)
        except ValueError:
            if parent_oids and not all_oids.intersection(parent_oids):
                terminal_commit = True
                active_lanes = [None]
                commit_column = 0
            else:
                commit_column = len(active_lanes)
                active_lanes.append(oid)
        commit_color = color_map.get(oid, None)
        if commit_color is None:
            commit_color = next_color
            next_color += 1
        else:
            color_map.pop(oid)
        edges = []
        for i, lane_oid in enumerate(active_lanes):
            if lane_oid is not None and lane_oid != oid:
                edges.append((i, i, color_map[lane_oid]))
        if parent_oids and not terminal_commit:
            for i, parent_oid in enumerate(parent_oids):
                parent_color = color_map.get(parent_oid, None)
                if parent_color is None:
                    if i == 0:
                        parent_color = commit_color
                    else:
                        parent_color = next_color
                        next_color += 1
                    color_map[parent_oid] = parent_color
                try:
                    parent_col = active_lanes.index(parent_oid)
                    if i == 0:
                        active_lanes[commit_column] = None
                except ValueError:
                    if i == 0:
                        active_lanes[commit_column] = parent_oid
                        parent_col = commit_column
                    elif None in active_lanes:
                        parent_col = active_lanes.index(None)
                        active_lanes[parent_col] = parent_oid
                    else:
                        parent_col = len(active_lanes)
                        active_lanes.append(parent_oid)
                edges.append((commit_column, parent_col, parent_color))
        else:
            active_lanes[commit_column] = None
        max_columns = max(max_columns, len(active_lanes))
        while active_lanes and active_lanes[-1] is None:
            active_lanes.pop()
        if head_oid is not None and oid == head_oid:
            color = GraphRowColor.HEAD
        elif len(parent_oids) > 1:
            color = GraphRowColor.MERGE
        else:
            color = GraphRowColor.NORMAL
        rows.append((oid, commit_column, tuple(edges), color))
    return rows, max_columns


def _actual_graph(result):
    rows = [
        (
            r.commit_oid,
            r.commit_column,
            tuple(
                (e.from_column, e.to_column, e.color_index) for e in r.edges_to_parent
            ),
            r.color,
        )
        for r in result.rows
    ]
    return rows, result.max_columns


def test_build_graph_matches_reference_on_random_dags():
    """The optimised build_graph matches the original algorithm exactly.

    Random topo-ordered DAGs -- including merges, octopus merges and terminal
    commits whose parents fall outside the loaded set -- are fed to both, and
    every row, edge, colour and the column count must agree.
    """
    import random

    rng = random.Random(20240611)
    for _ in range(400):
        n = rng.randint(0, 30)
        commits = []
        oids = []
        for i in range(n):
            oid = f'c{i}'
            if oids and rng.random() < 0.15:
                # Parents outside the loaded history -> a terminal commit.
                parents = [f'ext{i}']
            else:
                max_parents = min(3, len(oids))
                k = rng.randint(0, max_parents)
                parents = rng.sample(oids, k) if k else []
            commits.append((oid, parents))
            oids.append(oid)
        head = rng.choice(oids) if oids and rng.random() < 0.5 else None
        result = build_graph(commits, head_oid=head)
        assert _actual_graph(result) == _reference_graph(commits, head_oid=head)
