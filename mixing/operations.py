import json

from intake.source_book import LotBook, write_transaction
from intake.weighings import require_label
from publication.history import PublicationHistory

from .topology import resolve_flow
from .transfers import Transfer, complete_transfer, normalize_transfer


def initialize_operations(connection):
    connection.execute('''
        CREATE TABLE IF NOT EXISTS measured_operations (
            batch TEXT NOT NULL,
            revision TEXT NOT NULL,
            operation_id TEXT NOT NULL,
            measurements TEXT NOT NULL,
            PRIMARY KEY (batch, revision, operation_id)
        )
    ''')


def read_revision_operations(connection, batch, revision):
    rows = connection.execute('''
        SELECT operation_id, measurements FROM measured_operations
        WHERE batch = ? AND revision = ? ORDER BY operation_id
    ''', (batch, revision))
    result = []
    for operation_id, measurements in rows:
        inputs, outputs = json.loads(measurements)
        result.append(Transfer(
            operation_id,
            tuple((tuple(reference), weight) for reference, weight in inputs),
            tuple((load_id, weight) for load_id, weight in outputs),
        ))
    return tuple(result)


def read_lot_revisions(connection, operations):
    keys = sorted({
        reference[1:] for operation in operations for reference, _ in operation.inputs
        if reference[0] == 'lot'
    })
    book = LotBook(connection)
    lots = []
    for lot_id, revision in keys:
        lot = book.get_revision(lot_id, revision)
        if lot is None:
            raise ValueError(f'unknown lot revision {lot_id}/{revision}')
        lots.append(lot)
    return tuple(lots)


def record_transfer(connection, batch, revision, operation_id, inputs, outputs):
    batch = require_label(batch, 'batch')
    revision = require_label(revision, 'batch revision')
    candidate = normalize_transfer(operation_id, inputs, outputs)
    with write_transaction(connection):
        scope = {
            operation.operation_id: operation
            for operation in read_revision_operations(connection, batch, revision)
        }
        previous = scope.get(operation_id)
        if previous is not None:
            candidate = complete_transfer(previous, candidate)
            if candidate == previous:
                return previous
        if PublicationHistory(connection).get_by_revision(batch, revision) is not None:
            raise ValueError('sealed revision cannot be edited')

        prior_loads = {
            load_id for operation in scope.values() for load_id, _ in operation.outputs
        }
        for reference, _ in candidate.inputs:
            if reference[0] == 'load' and reference[1] not in prior_loads:
                raise ValueError(f'unknown input load {reference[1]}')

        scope[operation_id] = candidate
        lots = read_lot_revisions(connection, scope.values())
        weights = {(lot.lot_id, lot.revision): lot.kilograms for lot in lots}
        resolve_flow(scope.values(), weights)
        content = json.dumps([candidate.inputs, candidate.outputs], separators=(',', ':'))
        connection.execute('''
            INSERT INTO measured_operations (batch, revision, operation_id, measurements)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(batch, revision, operation_id)
            DO UPDATE SET measurements = excluded.measurements
        ''', (batch, revision, operation_id, content))
    return candidate
