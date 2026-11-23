from dataclasses import dataclass


@dataclass(frozen=True)
class ContainerLoad:
    load_id: str
    produced_by: str
    kilograms: int | None
    consumed_by: str | None = None


def resolve_input_load(load_id, loads):
    try:
        return loads[load_id]
    except KeyError as exc:
        raise ValueError(f'unknown input load {load_id}') from exc


def check_output_identity(load_id, loads):
    if load_id in loads:
        raise ValueError(f'load {load_id} already has a producer')
