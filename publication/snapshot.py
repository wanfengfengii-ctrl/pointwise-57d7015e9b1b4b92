from dataclasses import dataclass

from intake.revisions import LotRevision
from mixing.balance import require_complete_weighings
from mixing.containers import ContainerLoad
from mixing.operations import read_lot_revisions, read_revision_operations
from mixing.topology import resolve_flow, terminal_loads
from mixing.transfers import Transfer


@dataclass(frozen=True)
class FrozenSnapshot:
    batch: str
    revision: str
    lots: tuple[LotRevision, ...]
    operations: tuple[Transfer, ...]
    terminals: tuple[ContainerLoad, ...]


def capture_snapshot(connection, batch, revision):
    """Read the candidate within the same transaction that will issue it."""
    operations = read_revision_operations(connection, batch, revision)
    if not operations:
        raise ValueError('batch revision has no measured operations')
    for operation in operations:
        require_complete_weighings(operation)
    lots = read_lot_revisions(connection, operations)
    weights = {(lot.lot_id, lot.revision): lot.kilograms for lot in lots}
    flow = resolve_flow(operations, weights)
    return FrozenSnapshot(batch, revision, lots, flow.operations, terminal_loads(flow))
