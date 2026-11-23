import json

from intake.source_book import write_transaction
from intake.weighings import require_label


def initialize_voucher_station(connection, station_id):
    station_id = require_label(station_id, 'station')
    if connection.in_transaction:
        raise ValueError('station initialization requires an idle connection')
    existing = connection.execute(
        'SELECT station_id FROM voucher_station'
    ).fetchall()
    if existing:
        if existing[0][0] != station_id or len(existing) > 1:
            raise ValueError('settlement database is already bound to another station')
        return
    with write_transaction(connection):
        connection.execute(
            'INSERT INTO voucher_station (station_id) VALUES (?)', (station_id,),
        )


class VoucherStationLedger:
    def __init__(self, connection):
        self.connection = connection

    def station(self):
        row = self.connection.execute('SELECT station_id FROM voucher_station LIMIT 1').fetchone()
        return None if row is None else row[0]

    def find_voucher(self, voucher_id):
        return self.connection.execute('''
            SELECT seal_id, fingerprint, lot_id, buckets, kilograms, content
            FROM station_vouchers WHERE voucher_id = ?
        ''', (voucher_id,)).fetchone()

    def totals(self, voucher_id):
        row = self.connection.execute('''
            SELECT COALESCE(SUM(CASE WHEN kind = 'debit' THEN kilograms ELSE 0 END), 0) AS debited,
                   COALESCE(SUM(CASE WHEN kind = 'return' THEN kilograms ELSE 0 END), 0) AS returned
            FROM voucher_actions WHERE voucher_id = ?
        ''', (voucher_id,)).fetchone()
        return int(row[0]), int(row[1])

    def find_action(self, request_id):
        return self.connection.execute('''
            SELECT kind, content FROM voucher_actions WHERE request_id = ?
        ''', (request_id,)).fetchone()

    def register_voucher(self, document, content):
        self.connection.execute('''
            INSERT INTO station_vouchers
                (voucher_id, seal_id, fingerprint, lot_id, buckets, kilograms, content, identity)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (document['voucher_id'], document['seal_id'], document['fingerprint'],
              document['lot_id'], json.dumps(document['buckets'], separators=(',', ':')),
              str(document['kilograms']), content,
              json.dumps([document['seal_id'], document['fingerprint'], document['lot_id'],
                          document['buckets'], document['station_id'], document['kilograms']],
                         separators=(',', ':'))))

    def insert_action(self, kind, request_id, voucher_id, kilograms, content):
        self.connection.execute('''
            INSERT INTO voucher_actions (request_id, kind, voucher_id, kilograms, content)
            VALUES (?, ?, ?, ?, ?)
        ''', (request_id, kind, voucher_id, str(kilograms), content))


def initialize_voucher_tables(connection):
    # Called from initialize_settlement so an existing settlement database can
    # reopen with the voucher ledger while foreign keys stay enforced.
    connection.execute('''
        CREATE TABLE IF NOT EXISTS voucher_station (
            station_id TEXT PRIMARY KEY NOT NULL
        )
    ''')
    connection.execute('''
        CREATE TABLE IF NOT EXISTS station_vouchers (
            voucher_id TEXT PRIMARY KEY NOT NULL,
            seal_id TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            lot_id TEXT NOT NULL,
            buckets TEXT NOT NULL,
            kilograms TEXT NOT NULL,
            content BLOB NOT NULL,
            identity TEXT NOT NULL
        )
    ''')
    connection.execute('''
        CREATE TABLE IF NOT EXISTS voucher_actions (
            request_id TEXT PRIMARY KEY NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('debit', 'return')),
            voucher_id TEXT NOT NULL,
            kilograms TEXT NOT NULL,
            content BLOB NOT NULL
        )
    ''')
