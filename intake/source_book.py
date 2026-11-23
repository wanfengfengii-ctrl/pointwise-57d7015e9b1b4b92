from contextlib import contextmanager

from .revisions import LotRevision


@contextmanager
def write_transaction(connection):
    # Acquire the writer lock before reading any state used to make a decision.
    connection.execute('BEGIN IMMEDIATE')
    with connection:
        yield


def initialize_lot_book(connection):
    connection.execute('''
        CREATE TABLE IF NOT EXISTS lot_revisions (
            lot_id TEXT NOT NULL,
            revision TEXT NOT NULL,
            kilograms TEXT NOT NULL,
            receipt_id TEXT NOT NULL,
            PRIMARY KEY (lot_id, revision)
        )
    ''')


class LotBook:
    def __init__(self, connection):
        self.connection = connection

    def get_revision(self, lot_id, revision):
        row = self.connection.execute(
            'SELECT kilograms, receipt_id FROM lot_revisions WHERE lot_id = ? AND revision = ?',
            (lot_id, revision),
        ).fetchone()
        if row is None:
            return None
        return LotRevision(lot_id, revision, int(row[0]), row[1])

    def insert_revision(self, record: LotRevision):
        self.connection.execute(
            'INSERT INTO lot_revisions (lot_id, revision, kilograms, receipt_id) VALUES (?, ?, ?, ?)',
            (record.lot_id, record.revision, str(record.kilograms), record.receipt_id),
        )

    def write_transaction(self):
        return write_transaction(self.connection)
