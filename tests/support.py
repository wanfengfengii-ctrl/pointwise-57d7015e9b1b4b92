import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from assessment.accepted_batches import initialize_accepted_batches
from intake.lots import record_lot
from intake.source_book import initialize_lot_book
from mixing.operations import record_transfer, initialize_operations
from publication.freeze import seal
from publication.history import initialize_publications
from settlement.ledger import initialize_settlement


BATCH = 'day-01'


def lot(name, revision='r1'):
    return 'lot', name, revision


def load(name):
    return 'load', name


def receive(connection, amounts, revision='r1'):
    for name, weight in amounts.items():
        record_lot(connection, name, revision, weight, f'scale-ticket-{name}-{revision}')


def transfer(connection, operation, inputs, outputs, revision='r1', batch=BATCH):
    return record_transfer(connection, batch, revision, operation, inputs, outputs)


def mix(connection, *, a=6, b=4, revision='r1', zero=False, reverse=False):
    amounts = [('A', a), ('B', b)]
    outputs = [('X', 5), ('Y', 5)]
    if zero:
        outputs.append(('W', 0))
    if reverse:
        amounts.reverse()
        outputs.reverse()
    receive(connection, dict(amounts), revision)
    transfer(connection, 'split', {lot(name, revision): weight for name, weight in amounts},
             dict(outputs), revision)


def fixed(connection, revision='r1', batch=BATCH):
    return seal(connection, batch, revision)


def assert_rows(test, connection, **counts):
    for table, count in counts.items():
        with test.subTest(table=table):
            observed = connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
            test.assertEqual(observed, count)


class WarehouseCase(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(TemporaryDirectory())
        self.root = Path(folder)
        self.source = self.open_source(self.root / 'source.db')
        self.center = self.open_center()

    def open_source(self, path):
        db = sqlite3.connect(path)
        self.addCleanup(db.close)
        initialize_lot_book(db)
        initialize_operations(db)
        initialize_publications(db)
        return db

    def open_center(self):
        db = sqlite3.connect(self.root / 'center.db')
        self.addCleanup(db.close)
        initialize_accepted_batches(db)
        initialize_settlement(db)
        return db

    def reopen_center(self):
        self.center.close()
        self.center = self.open_center()
