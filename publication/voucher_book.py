import json

from intake.source_book import write_transaction
from intake.weighings import require_label
from mixing.assignment import jointly_feasible, minimum_lot_in_group
from publication.packages import decode_v1_package
from publication.vouchers import (
    decode_return,
    decode_voucher,
    encode_return,
    encode_voucher,
    normalize_bucket_group,
    require_positive_kg,
)

from .history import PublicationHistory


class VoucherBook:
    def __init__(self, connection):
        self.connection = connection

    def find_voucher(self, voucher_id):
        return self.connection.execute(
            'SELECT seal_id, lot_id, buckets, station_id, kilograms, content '
            'FROM issued_vouchers WHERE voucher_id = ?',
            (voucher_id,),
        ).fetchone()

    def confirmed_total(self, voucher_id):
        row = self.connection.execute(
            'SELECT COALESCE(SUM(kilograms), 0) FROM confirmed_returns WHERE voucher_id = ?',
            (voucher_id,),
        ).fetchone()
        return int(row[0])

    def find_return(self, station_id, request_id):
        return self.connection.execute(
            'SELECT voucher_id, kilograms, content FROM confirmed_returns '
            'WHERE station_id = ? AND request_id = ?',
            (station_id, request_id),
        ).fetchone()

    def outstanding_commitments(self, seal_id, lot_id):
        rows = self.connection.execute('''
            SELECT v.voucher_id, v.buckets, v.kilograms,
                   COALESCE((SELECT SUM(r.kilograms) FROM confirmed_returns AS r
                             WHERE r.voucher_id = v.voucher_id), 0) AS returned
            FROM issued_vouchers AS v
            WHERE v.seal_id = ? AND v.lot_id = ?
        ''', (seal_id, lot_id)).fetchall()
        return [
            (tuple(json.loads(buckets)), int(kilograms) - int(returned))
            for _, buckets, kilograms, returned in rows
        ]

    def insert_voucher(self, voucher_id, seal_id, lot_id, buckets, station_id, kilograms, content):
        self.connection.execute('''
            INSERT INTO issued_vouchers
                (voucher_id, seal_id, lot_id, buckets, station_id, kilograms, content, identity)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (voucher_id, seal_id, lot_id, json.dumps(buckets, separators=(',', ':')),
              station_id, str(kilograms), content,
              json.dumps([seal_id, lot_id, buckets, station_id, kilograms],
                         separators=(',', ':'))))

    def insert_return(self, station_id, request_id, voucher_id, kilograms, content):
        self.connection.execute('''
            INSERT INTO confirmed_returns
                (station_id, request_id, voucher_id, kilograms, content)
            VALUES (?, ?, ?, ?, ?)
        ''', (station_id, request_id, voucher_id, str(kilograms), content))


def issue_voucher(connection, voucher_id, seal_id, lot_id, buckets, station_id, kilograms):
    voucher_id = require_label(voucher_id, 'voucher')
    seal_id = require_label(seal_id, 'sealed batch')
    lot_id = require_label(lot_id, 'source lot')
    station_id = require_label(station_id, 'station')
    group = normalize_bucket_group(buckets)
    kilograms = require_positive_kg(kilograms)
    with write_transaction(connection):
        book = VoucherBook(connection)
        existing = book.find_voucher(voucher_id)
        if existing is not None:
            stored_seal, stored_lot, stored_buckets, stored_station, stored_kg, content = existing
            identity = (stored_seal, stored_lot, tuple(json.loads(stored_buckets)),
                        stored_station, int(stored_kg))
            if identity != (seal_id, lot_id, group, station_id, kilograms):
                raise ValueError(f'conflicting fixed voucher {voucher_id}')
            return content

        issued = PublicationHistory(connection).get_issued(seal_id)
        if issued is None:
            raise KeyError(seal_id)
        evidence = decode_v1_package(issued.data).evidence
        # Rejects unknown source lots and unknown terminal buckets.
        minimum_lot_in_group(evidence, lot_id, group)

        commitments = [*book.outstanding_commitments(seal_id, lot_id), (group, kilograms)]
        if not jointly_feasible(evidence, lot_id, commitments):
            raise ValueError(
                f'voucher {voucher_id} cannot be honoured jointly with outstanding commitments'
            )
        content = encode_voucher(
            voucher_id=voucher_id, seal_id=seal_id,
            fingerprint=issued.receipt.fingerprint, lot_id=lot_id, buckets=group,
            station_id=station_id, kilograms=kilograms,
        )
        book.insert_voucher(voucher_id, seal_id, lot_id, group, station_id, kilograms, content)
    return content


def confirm_return(connection, return_data):
    document = decode_return(return_data)
    with write_transaction(connection):
        book = VoucherBook(connection)
        previous = book.find_return(document['station_id'], document['request_id'])
        if previous is not None:
            voucher_id, kilograms, content = previous
            if content != return_data or voucher_id != document['voucher_id'] \
                    or int(kilograms) != document['kilograms']:
                raise ValueError('return request identity conflicts with a confirmed document')
            return {'station_id': document['station_id'], 'request_id': document['request_id'],
                    'voucher_id': voucher_id, 'kilograms': int(kilograms)}

        stored = book.find_voucher(document['voucher_id'])
        if stored is None:
            raise ValueError(f'unknown voucher {document["voucher_id"]}')
        seal_id, lot_id, stored_buckets, station_id, voucher_kg, voucher_content = stored
        fixed = decode_voucher(voucher_content)
        expected = (seal_id, fixed['fingerprint'], lot_id, tuple(json.loads(stored_buckets)),
                    station_id)
        actual = (document['seal_id'], document['fingerprint'], document['lot_id'],
                  document['buckets'], document['station_id'])
        if expected != actual:
            raise ValueError('return document does not match the issued voucher')
        already = book.confirmed_total(document['voucher_id'])
        if already + document['kilograms'] > int(voucher_kg):
            raise ValueError('confirmed returns exceed the voucher amount')

        content = encode_return(
            station_id=document['station_id'], request_id=document['request_id'],
            voucher_id=document['voucher_id'], seal_id=document['seal_id'],
            fingerprint=document['fingerprint'], lot_id=document['lot_id'],
            buckets=document['buckets'], kilograms=document['kilograms'],
        )
        if content != return_data:
            raise ValueError('return document is not the fixed station record')
        book.insert_return(document['station_id'], document['request_id'],
                           document['voucher_id'], document['kilograms'], content)
    return {'station_id': document['station_id'], 'request_id': document['request_id'],
            'voucher_id': document['voucher_id'], 'kilograms': document['kilograms']}
