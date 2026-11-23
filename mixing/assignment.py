from itertools import combinations

from .balance import maximum_flow


def _network(evidence):
    source = evidence.operation_count
    sink = source + 1
    edges = [(source, node, weight) for _, node, weight in evidence.sources]
    edges.extend(evidence.arcs)
    terminals = {name: (node, weight) for name, node, weight in evidence.terminals}
    admitted = {lot_id: weight for lot_id, _, weight in evidence.sources}
    return source, sink, edges, terminals, admitted


def minimum_lot_in_group(evidence, lot_id, bucket_names):
    """Smallest amount of the lot that every integral allocation leaves in the group.

    Mass that cannot be routed into any bucket outside the group must appear
    inside it, so the guaranteed minimum is the admitted total minus the
    maximum flow reachable through the complement terminals.
    """
    source, sink, edges, terminals, admitted = _network(evidence)
    if lot_id not in admitted:
        raise ValueError(f'unknown source lot {lot_id}')
    group = set(bucket_names)
    if not group:
        raise ValueError('bucket group must be nonempty')
    unknown = group - set(terminals)
    if unknown:
        raise ValueError(f'unknown terminal bucket {sorted(unknown)[0]}')
    complement = [
        (node, sink, weight)
        for name, (node, weight) in terminals.items()
        if name not in group
    ]
    outside = maximum_flow([*edges, *complement], source, sink)
    return admitted[lot_id] - outside


def jointly_feasible(evidence, lot_id, commitments):
    """Hall test for serving every outstanding promise under every allocation.

    ``commitments`` maps each voucher to ``(bucket_tuple, outstanding_kg)``.
    For any subset of vouchers their combined promise can only draw from the
    union of their bucket groups, so it cannot exceed the amount of the lot
    guaranteed to appear in that union in every integral allocation. Each
    promised kilogram is then assignable to exactly one voucher.
    """
    groups = tuple((frozenset(names), amount) for names, amount in commitments)
    minima = {}

    def guaranteed(names):
        key = frozenset(names)
        if key not in minima:
            minima[key] = minimum_lot_in_group(evidence, lot_id, key)
        return minima[key]

    indices = tuple(range(len(groups)))
    for size in range(1, len(groups) + 1):
        for subset in combinations(indices, size):
            union = frozenset()
            demand = 0
            for index in subset:
                names, amount = groups[index]
                union |= names
                demand += amount
            if guaranteed(union) < demand:
                return False
    return True
