from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

from intake.weighings import require_kilograms, require_label

if TYPE_CHECKING:
    from .snapshot import FrozenSnapshot


@dataclass(frozen=True)
class JointEvidence:
    operation_count: int
    sources: tuple[tuple[str, int, int], ...]
    arcs: tuple[tuple[int, int, int], ...]
    terminals: tuple[tuple[str, int, int], ...]

    @property
    def buckets(self):
        return tuple((name, weight) for name, _, weight in self.terminals)


def build_evidence(snapshot: 'FrozenSnapshot'):
    output_nodes = {}
    public_sources = []
    internal_arcs = []
    weights = {(lot.lot_id, lot.revision): lot.kilograms for lot in snapshot.lots}
    for node, operation in enumerate(snapshot.operations):
        for reference, weight in operation.inputs:
            if reference[0] == 'lot':
                public_sources.append((reference[1], node, weights[reference[1:]]))
            else:
                internal_arcs.append((output_nodes[reference[1]], node, weight))
        for load_id, _ in operation.outputs:
            output_nodes[load_id] = node
    terminals = tuple(sorted(
        (load.load_id, output_nodes[load.load_id], load.kilograms) for load in snapshot.terminals
    ))
    evidence = JointEvidence(
        len(snapshot.operations), tuple(sorted(public_sources)), tuple(sorted(internal_arcs)), terminals,
    )
    validate_evidence(evidence)
    return evidence


def validate_evidence(evidence):
    count = evidence.operation_count
    if type(count) is not int or count < 1 or not evidence.sources or not evidence.terminals:
        raise ValueError('incomplete material evidence')

    def require_node(node):
        if type(node) is not int or not 0 <= node < count:
            raise ValueError('invalid material node')
        return node

    incoming = Counter()
    outgoing = Counter()
    source_ids = set()
    bucket_ids = set()
    for name, node, weight in evidence.sources:
        require_label(name, 'source lot')
        if name in source_ids:
            raise ValueError(f'duplicate source lot {name}')
        source_ids.add(name)
        incoming[require_node(node)] += require_kilograms(weight)
    for start, end, weight in evidence.arcs:
        start, end = require_node(start), require_node(end)
        weight = require_kilograms(weight)
        if start >= end:
            raise ValueError('material arc violates the dependency order')
        outgoing[start] += weight
        incoming[end] += weight
    for name, node, weight in evidence.terminals:
        require_label(name, 'terminal bucket')
        if name in bucket_ids:
            raise ValueError(f'duplicate terminal bucket {name}')
        bucket_ids.add(name)
        outgoing[require_node(node)] += require_kilograms(weight)

    # Compare support as well as mass. Zero-kg arcs still identify real loads.
    if len(incoming) != count or incoming.keys() != outgoing.keys() or incoming != outgoing:
        raise ValueError('incompatible material evidence')
    # A balanced DAG admits a complete joint assignment: route the incoming
    # lot quantities into the fixed outgoing capacities at each node in order.
