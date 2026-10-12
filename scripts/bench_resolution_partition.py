"""Offline P7 benchmark for empty-cannot-link partitioning (no database/API).

Run from the repository root:
    python -m scripts.bench_resolution_partition --sizes 1000 5000 10000 --repeat 3

Compare the original loop with the current helper on deterministic shuffled
mention IDs. Membership counts are measured separately from wall timings so
the counter's Python overhead does not inflate the before/after timing.
No timing threshold is an assertion; equal outputs and exact work counts are.
"""

from __future__ import annotations

import argparse
import json
import platform
import random
from statistics import median
from time import perf_counter

from src.pipeline.resolve_core import _partition_on_cannot_links
from src.resolve.review import ordered_pair


class CountingConstraints(set):
    """Count actual set membership probes, not an estimated work formula."""

    def __init__(self, values=()):
        super().__init__(values)
        self.probes = 0

    def __contains__(self, value):
        self.probes += 1
        return super().__contains__(value)


def legacy_partition(members, cannot_links):
    """Original helper from base commit 3a9bd697, preserved for comparison."""
    partitions = []
    for mention_id in sorted(members):
        for partition in partitions:
            if all(ordered_pair(mention_id, other) not in cannot_links for other in partition):
                partition.append(mention_id)
                break
        else:
            partitions.append([mention_id])
    return partitions


def benchmark(size: int, repeat: int) -> dict:
    members = list(range(1, size + 1))
    random.Random(42).shuffle(members)
    before_constraints = CountingConstraints()
    after_constraints = CountingConstraints()
    before = legacy_partition(members, before_constraints)
    after = _partition_on_cannot_links(members, after_constraints)
    assert before == after == ([sorted(members)] if members else [])
    assert before_constraints.probes == size * (size - 1) // 2
    assert after_constraints.probes == 0

    timings = {"legacy": [], "fast_path": []}
    for _ in range(repeat):
        for label, operation in (
            ("legacy", legacy_partition),
            ("fast_path", _partition_on_cannot_links),
        ):
            started = perf_counter()
            result = operation(members, set())
            timings[label].append(perf_counter() - started)
            assert result == before

    return {
        "members": size,
        "outputs_equal": True,
        "legacy_membership_checks": before_constraints.probes,
        "fast_path_membership_checks": after_constraints.probes,
        "legacy_median_seconds": median(timings["legacy"]),
        "fast_path_median_seconds": median(timings["fast_path"]),
        "repeat": repeat,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", nargs="+", type=int, default=[1000, 5000, 10000])
    parser.add_argument("--repeat", type=int, default=3)
    args = parser.parse_args()
    if args.repeat < 1 or any(size < 0 for size in args.sizes):
        parser.error("sizes must be non-negative and repeat must be positive")
    print(json.dumps({"python": platform.python_version(), "platform": platform.system()}))
    for size in args.sizes:
        print(json.dumps(benchmark(size, args.repeat), sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
