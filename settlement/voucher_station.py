import json

from assessment.accepted_batches import AcceptedBatchStore
from intake.source_book import write_transaction
from intake.weighings import require_label
from publication.vouchers import decode_return, decode_voucher, encode_return, require_positive_kg

from .voucher_ledger import VoucherStationLedger, initialize_voucher_station

__all__ = [
    'initialize_voucher_station',
    'debit_voucher',
    'return_voucher',
    'retrieve_voucher_action',
]


def _admit_voucher(connection, ledger, voucher_data, document):
    station_id = ledger.station()
    if station_id is None:
        raise ValueError('settlement database is not bound to a station')
    if document['station_id'] != station_id:
        raise ValueError('voucher was issued to a different station')
    accepted = AcceptedBatchStore(connection).get(document['seal_id'])
    if accepted is None:
        raise ValueError(f'no accepted sealed batch {document["seal_id"]} at this station')
    if accepted.fingerprint != document['fingerprint']:
        raise ValueError('voucher fingerprint disagrees with the accepted sealed batch')
    terminals = {name for name, _ in accepted.package.buckets}
    if set(document['buckets']) - terminals:
        raise ValueError('voucher references a bucket absent from the sealed batch')
    if document['lot_id'] not in {name for name, _, _ in accepted.package.evidence.sources}:
        raise ValueError(f'unknown source lot {document["lot_id"]}')

    existing = ledger.find_voucher(document['voucher_id'])
    if existing is not None:
        seal_id, fingerprint, lot_id, buckets, kilograms, content = existing
        identity = (seal_id, fingerprint, lot_id, tuple(json.loads(buckets)),
                    int(kilograms), content)
        if identity != (document['seal_id'], document['fingerprint'], document['lot_id'],
                        document['buckets'], document['kilograms'], voucher_data):
            raise ValueError(f'voucher {document["voucher_id"]} was rebound to different parameters')
    else:
        ledger.register_voucher(document, voucher_data)
    return document


def _replay_or_conflict(ledger, request_id, kind, voucher_id, kilograms):
    previous = ledger.find_action(request_id)
    if previous is None:
        return None
    stored_kind, content = previous
    if stored_kind != kind:
        raise ValueError(f'request {request_id} is already bound to a different action kind')
    if kind == 'debit':
        result = json.loads(content)
        if result['voucher_id'] != voucher_id or result['kilograms'] != kilograms:
            raise ValueError(f'request {request_id} is already bound to different parameters')
        result['buckets'] = tuple(result['buckets'])
        return result
    document = decode_return(content)
    if document['voucher_id'] != voucher_id or document['kilograms'] != kilograms:
        raise ValueError(f'request {request_id} is already bound to different parameters')
    return content


def _run(connection, voucher_data, request_id, kilograms, kind):
    request_id = require_label(request_id, 'request')
    kilograms = require_positive_kg(kilograms)
    document = decode_voucher(voucher_data)
    with write_transaction(connection):
        ledger = VoucherStationLedger(connection)
        replayed = _replay_or_conflict(
            ledger, request_id, kind, document['voucher_id'], kilograms,
        )
        if replayed is not None:
            return replayed
        _admit_voucher(connection, ledger, voucher_data, document)
        debited, returned = ledger.totals(document['voucher_id'])
        if debited + returned + kilograms > document['kilograms']:
            raise ValueError('voucher amount requested exceeds the amount still available')
        if kind == 'debit':
            receipt = {
                'station_id': document['station_id'],
                'request_id': request_id,
                'voucher_id': document['voucher_id'],
                'seal_id': document['seal_id'],
                'fingerprint': document['fingerprint'],
                'lot_id': document['lot_id'],
                'buckets': list(document['buckets']),
                'kilograms': kilograms,
            }
            content = json.dumps(receipt, sort_keys=True, separators=(',', ':'), allow_nan=False)
        else:
            content = encode_return(
                station_id=document['station_id'], request_id=request_id,
                voucher_id=document['voucher_id'], seal_id=document['seal_id'],
                fingerprint=document['fingerprint'], lot_id=document['lot_id'],
                buckets=document['buckets'], kilograms=kilograms,
            )
        ledger.insert_action(kind, request_id, document['voucher_id'], kilograms, content)
    if kind == 'debit':
        receipt['buckets'] = tuple(receipt['buckets'])
        return receipt
    return content


def debit_voucher(connection, voucher_data, request_id, kilograms):
    return _run(connection, voucher_data, request_id, kilograms, 'debit')


def return_voucher(connection, voucher_data, request_id, kilograms):
    return _run(connection, voucher_data, request_id, kilograms, 'return')


def retrieve_voucher_action(connection, request_id):
    request_id = require_label(request_id, 'request')
    row = VoucherStationLedger(connection).find_action(request_id)
    if row is None:
        raise KeyError(request_id)
    kind, content = row
    if kind == 'debit':
        result = json.loads(content)
        result['buckets'] = tuple(result['buckets'])
        return result
    return content
