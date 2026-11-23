from dataclasses import dataclass, replace
from heapq import heapify, heappop, heappush

from .balance import check_operation_balance, require_feasible_measures
from .containers import ContainerLoad, check_output_identity, resolve_input_load
from .transfers import Transfer, validate_consumption


@dataclass(frozen=True)
class ResolvedFlow:
    operations: tuple[Transfer, ...]
    loads: tuple[ContainerLoad, ...]


def terminal_loads(flow):
    return tuple(load for load in flow.loads if load.consumed_by is None)


def _dependency_order(operations, dependencies):
    children = {operation_id: [] for operation_id in operations}
    remaining = {}
    for operation_id, parents in dependencies.items():
        remaining[operation_id] = len(parents)
        for parent in parents:
            children[parent].append(operation_id)

    ready = [key for key, count in remaining.items() if count == 0]
    heapify(ready)
    ordered = []
    while ready:
        current = heappop(ready)
        ordered.append(operations[current])
        for child in children[current]:
            remaining[child] -= 1
            if remaining[child] == 0:
                heappush(ready, child)
    if len(ordered) != len(operations):
        raise ValueError('cyclic material flow')
    return tuple(ordered)


def resolve_flow(operations, lot_weights):
    operations = tuple(operations)
    by_id = {operation.operation_id: operation for operation in operations}
    if len(by_id) != len(operations):
        raise ValueError('duplicate operation identity')

    loads = {}
    for operation in operations:
        check_operation_balance(operation)
        for load_id, weight in operation.outputs:
            check_output_identity(load_id, loads)
            loads[load_id] = ContainerLoad(load_id, operation.operation_id, weight)

    consumers = validate_consumption(operations)
    dependencies = {operation.operation_id: set() for operation in operations}
    peer_weights = {}
    received = {operation.operation_id: 0 for operation in operations}
    for operation in operations:
        for reference, input_weight in operation.inputs:
            if reference[0] == 'lot':
                try:
                    expected = lot_weights[reference[1:]]
                except KeyError as exc:
                    raise ValueError(f'unknown lot revision {reference[1:]}') from exc
                received[operation.operation_id] += expected
            else:
                load = resolve_input_load(reference[1], loads)
                dependencies[operation.operation_id].add(load.produced_by)
                expected = load.kilograms
                peer_weights[load.load_id] = input_weight
            if expected is not None and input_weight is not None and expected != input_weight:
                raise ValueError(f'conflicting input weighing for {reference}')

    ordered = _dependency_order(by_id, dependencies)
    indexes = {operation.operation_id: index for index, operation in enumerate(ordered)}
    loads = tuple(
        replace(load, consumed_by=consumers.get(('load', load.load_id)))
        for _, load in sorted(loads.items())
    )
    legs = []
    for load in loads:
        consumer = indexes[load.consumed_by] if load.consumed_by is not None else len(ordered)
        weight = load.kilograms if load.kilograms is not None else peer_weights.get(load.load_id)
        legs.append((indexes[load.produced_by], consumer, weight))
    supplies = [received[operation.operation_id] for operation in ordered]
    require_feasible_measures(supplies, legs)
    return ResolvedFlow(ordered, loads)
