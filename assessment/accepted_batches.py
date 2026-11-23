from dataclasses import dataclass

from publication.packages import V1Package, decode_v1_package, encode_v1_package, fingerprint_package


@dataclass(frozen=True)
class AcceptedBatch:
    package: V1Package
    fingerprint: str
    data: bytes

    @property
    def seal_id(self):
        return self.package.seal_id


def initialize_accepted_batches(connection):
    connection.execute('''
        CREATE TABLE IF NOT EXISTS accepted_batches (
            seal_id TEXT PRIMARY KEY NOT NULL,
            fingerprint TEXT NOT NULL,
            data BLOB NOT NULL
        )
    ''')


class AcceptedBatchStore:
    def __init__(self, connection):
        self.connection = connection

    def store_once(self, package):
        data = encode_v1_package(package)
        fingerprint = fingerprint_package(package)
        self.connection.execute('''
            INSERT INTO accepted_batches (seal_id, fingerprint, data) VALUES (?, ?, ?)
            ON CONFLICT(seal_id) DO NOTHING
        ''', (package.seal_id, fingerprint, data))
        existing = self.get(package.seal_id)
        if existing.fingerprint != fingerprint or existing.data != data:
            raise ValueError(f'conflicting accepted package {package.seal_id}')
        return existing

    def get(self, seal_id):
        row = self.connection.execute(
            'SELECT fingerprint, data FROM accepted_batches WHERE seal_id = ?', (seal_id,),
        ).fetchone()
        if row is None:
            return None
        fingerprint, data = row
        package = decode_v1_package(data)
        if seal_id != package.seal_id or fingerprint != fingerprint_package(package):
            raise ValueError('accepted archive differs from its fixed identity')
        return AcceptedBatch(package, fingerprint, data)
