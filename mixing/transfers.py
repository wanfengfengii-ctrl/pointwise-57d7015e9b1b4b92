from collections.abc import Mapping
from dataclasses import dataclass

from intake.weighings import require_kilograms, require_label


@dataclass(frozen=True)
class Transfer:
    operation_id: str
    inputs: tuple[tuple[tuple[str, ...], int | None], ...]
    outputs: tuple[tuple[str, int | None], ...]

    @property
    def state(self):
        if any(weight is None for _, weight in self.inputs + self.outputs):
            return 'pending_weighings'
        return 'ready'


def _normalize_reference(reference):
    if isinstance(reference, tuple):
        if len(reference) == 2 and reference[0] == 'load':
            return 'load', require_label(reference[1], 'input load')
        if len(reference) == 3 and reference[0] == 'lot':
            return 'lot', require_label(reference[1], 'input lot'), require_label(reference[2], 'lot revision')
    raise ValueError('input reference must identify a lot revision or a load')


def normalize_transfer(operation_id, inputs, outputs):
    operation_id = require_label(operation_id, 'operation')
    if not isinstance(inputs, Mapping) or not inputs:
        raise ValueError('operation requires at least one input')
    if not isinstance(outputs, Mapping) or not outputs:
        raise ValueError('operation requires at least one output')
    normalized_inputs = tuple(sorted(
        (_normalize_reference(reference), require_kilograms(weight, allow_missing=True))
        for reference, weight in inputs.items()
    ))
    normalized_outputs = tuple(sorted(
        (require_label(load_id, 'output load'), require_kilograms(weight, allow_missing=True))
        for load_id, weight in outputs.items()
    ))
    return Transfer(operation_id, normalized_inputs, normalized_outputs)


def validate_consumption(operations):
    consumers = {}
    for operation in operations:
        for reference, _ in operation.inputs:
            # A lot revision identifies the full admission of that lot in this scope.
            key = reference[:2]
            if key in consumers:
                raise ValueError(f'duplicate consumption of {reference[0]} {reference[1]}')
            consumers[key] = operation.operation_id
    return consumers


def _complete_weights(previous, incoming):
    if tuple(key for key, _ in previous) != tuple(key for key, _ in incoming):
        raise ValueError('operation flow cannot change under the same identity')
    completed = []
    for (key, old), (_, new) in zip(previous, incoming, strict=True):
        if old is not None and new is not None and old != new:
            raise ValueError(f'conflicting weighing for {key}')
        completed.append((key, old if old is not None else new))
    return tuple(completed)


def complete_transfer(previous, incoming):
    return Transfer(
        previous.operation_id,
        _complete_weights(previous.inputs, incoming.inputs),
        _complete_weights(previous.outputs, incoming.outputs),
    )
