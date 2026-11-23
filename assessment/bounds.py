from dataclasses import dataclass

from mixing.balance import maximum_flow
from publication.evidence import validate_evidence

from .requests import validate_selection


@dataclass(frozen=True)
class BoundsResult:
    original_kg: int
    group: tuple[int, int]
    buckets: tuple[tuple[str, int, int], ...]


def compute_bounds(accepted, request):
    validate_selection(request, accepted)
    evidence = accepted.package.evidence
    validate_evidence(evidence)
    _, admission, admitted_kg = next(row for row in evidence.sources if row[0] == request.lot_id)
    source = evidence.operation_count
    sink = source + 1
    internal = [(source, admission, admitted_kg), *evidence.arcs]
    terminals = {name: (node, weight) for name, node, weight in evidence.terminals}
    all_buckets = set(terminals)

    def max_to(names):
        edges = [*internal, *((node, sink, weight)
                              for name, (node, weight) in terminals.items() if name in names)]
        return maximum_flow(edges, source, sink)

    def range_for(names):
        # A partial target-lot flow to these outlets can be extended. At a
        # balanced operation the leftover incoming and outgoing capacities
        # agree. In dependency order they can carry the rest of this lot
        # and the other lots, filling every measured edge exactly.
        high = max_to(names)
        low = admitted_kg - max_to(all_buckets - names)
        return low, high

    group = range_for(set(request.buckets))
    buckets = tuple((name, *range_for({name})) for name in request.buckets)
    original_kg = sum(terminals[name][1] for name in request.buckets)
    return BoundsResult(original_kg, group, buckets)
