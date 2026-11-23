import json
from dataclasses import asdict, dataclass

from .packages import encode_v1_package, fingerprint_package


@dataclass(frozen=True)
class SealReceipt:
    seal_id: str
    batch: str
    revision: str
    fingerprint: str


@dataclass(frozen=True)
class IssuedPublication:
    data: bytes
    receipt: SealReceipt


def initialize_publications(connection):
    connection.execute('''
        CREATE TABLE IF NOT EXISTS issued_batches (
            seal_id TEXT PRIMARY KEY NOT NULL,
            batch TEXT NOT NULL,
            revision TEXT NOT NULL,
            data BLOB NOT NULL,
            receipt TEXT NOT NULL,
            UNIQUE (batch, revision)
        )
    ''')
    connection.execute('''
        CREATE TABLE IF NOT EXISTS issued_vouchers (
            voucher_id TEXT PRIMARY KEY NOT NULL,
            seal_id TEXT NOT NULL,
            lot_id TEXT NOT NULL,
            buckets TEXT NOT NULL,
            station_id TEXT NOT NULL,
            kilograms TEXT NOT NULL,
            content BLOB NOT NULL,
            identity TEXT NOT NULL
        )
    ''')
    connection.execute('''
        CREATE TABLE IF NOT EXISTS confirmed_returns (
            station_id TEXT NOT NULL,
            request_id TEXT NOT NULL,
            voucher_id TEXT NOT NULL,
            kilograms TEXT NOT NULL,
            content BLOB NOT NULL,
            PRIMARY KEY (station_id, request_id)
        )
    ''')


class PublicationHistory:
    def __init__(self, connection):
        self.connection = connection

    @staticmethod
    def _issued(row):
        if row is None:
            return None
        return IssuedPublication(row[0], SealReceipt(**json.loads(row[1])))

    def get_issued(self, seal_id):
        return self._issued(self.connection.execute(
            'SELECT data, receipt FROM issued_batches WHERE seal_id = ?', (seal_id,),
        ).fetchone())

    def get_by_revision(self, batch, revision):
        return self._issued(self.connection.execute(
            'SELECT data, receipt FROM issued_batches WHERE batch = ? AND revision = ?',
            (batch, revision),
        ).fetchone())

    def issue_once(self, package):
        data = encode_v1_package(package)
        receipt = SealReceipt(package.seal_id, package.batch, package.revision, fingerprint_package(package))
        self.connection.execute('''
            INSERT INTO issued_batches (seal_id, batch, revision, data, receipt)
            VALUES (?, ?, ?, ?, ?) ON CONFLICT(seal_id) DO NOTHING
        ''', (package.seal_id, package.batch, package.revision, data, json.dumps(asdict(receipt))))
        existing = self.get_issued(package.seal_id)
        if existing != IssuedPublication(data, receipt):
            raise ValueError(f'conflicting fixed publication {package.seal_id}')
        return existing


# Voucher issuing and return confirmation live in voucher_book; re-export them
# here so publication.history remains the single source-side entry point.
from .voucher_book import confirm_return, issue_voucher  # noqa: E402

__all__ = [
    'SealReceipt',
    'IssuedPublication',
    'initialize_publications',
    'PublicationHistory',
    'issue_voucher',
    'confirm_return',
]
