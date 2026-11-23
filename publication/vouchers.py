import hashlib
import json

from intake.weighings import require_label


def _canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('ascii')


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'duplicate document field {key}')
        result[key] = value
    return result


def require_positive_kg(value):
    # Booleans are integers in Python but are never valid measurements here.
    if type(value) is not int or value <= 0:
        raise ValueError('voucher amount must be a positive integer in kg')
    return value


def normalize_bucket_group(buckets):
    if not isinstance(buckets, (list, tuple)) or not buckets:
        raise ValueError('voucher requires a nonempty list or tuple of terminal buckets')
    names = tuple(require_label(name, 'bucket') for name in buckets)
    if len(set(names)) != len(names):
        raise ValueError('voucher bucket group contains a duplicate bucket')
    return tuple(sorted(names))


def _digest(document):
    payload = {key: value for key, value in document.items() if key != 'digest'}
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def encode_voucher(*, voucher_id, seal_id, fingerprint, lot_id, buckets, station_id, kilograms):
    document = {
        'kind': 'voucher',
        'version': 1,
        'voucher_id': require_label(voucher_id, 'voucher'),
        'seal_id': require_label(seal_id, 'sealed batch'),
        'fingerprint': require_label(fingerprint, 'fingerprint'),
        'lot_id': require_label(lot_id, 'source lot'),
        'buckets': list(normalize_bucket_group(buckets)),
        'station_id': require_label(station_id, 'station'),
        'kilograms': require_positive_kg(kilograms),
    }
    document['digest'] = _digest(document)
    return _canonical_json(document)


def decode_voucher(data):
    fields = {'kind', 'version', 'voucher_id', 'seal_id', 'fingerprint', 'lot_id',
              'buckets', 'station_id', 'kilograms', 'digest'}
    document = _decode_document(data, fields, 'voucher')
    return {
        'voucher_id': document['voucher_id'],
        'seal_id': document['seal_id'],
        'fingerprint': document['fingerprint'],
        'lot_id': document['lot_id'],
        'buckets': normalize_bucket_group(document['buckets']),
        'station_id': document['station_id'],
        'kilograms': require_positive_kg(document['kilograms']),
        'digest': document['digest'],
    }


def encode_return(*, station_id, request_id, voucher_id, seal_id, fingerprint, lot_id,
                  buckets, kilograms):
    document = {
        'kind': 'voucher_return',
        'version': 1,
        'station_id': require_label(station_id, 'station'),
        'request_id': require_label(request_id, 'request'),
        'voucher_id': require_label(voucher_id, 'voucher'),
        'seal_id': require_label(seal_id, 'sealed batch'),
        'fingerprint': require_label(fingerprint, 'fingerprint'),
        'lot_id': require_label(lot_id, 'source lot'),
        'buckets': list(normalize_bucket_group(buckets)),
        'kilograms': require_positive_kg(kilograms),
    }
    document['digest'] = _digest(document)
    return _canonical_json(document)


def decode_return(data):
    fields = {'kind', 'version', 'station_id', 'request_id', 'voucher_id', 'seal_id',
              'fingerprint', 'lot_id', 'buckets', 'kilograms', 'digest'}
    document = _decode_document(data, fields, 'voucher_return')
    return {
        'station_id': document['station_id'],
        'request_id': document['request_id'],
        'voucher_id': document['voucher_id'],
        'seal_id': document['seal_id'],
        'fingerprint': document['fingerprint'],
        'lot_id': document['lot_id'],
        'buckets': normalize_bucket_group(document['buckets']),
        'kilograms': require_positive_kg(document['kilograms']),
        'digest': document['digest'],
    }


def _decode_document(data, fields, kind):
    if not isinstance(data, (bytes, str)):
        raise ValueError(f'{kind} input must be JSON bytes or text')
    try:
        document = json.loads(data, object_pairs_hook=_unique_object)
    except (ValueError, RecursionError) as exc:
        raise ValueError(f'invalid {kind} JSON') from exc
    if not isinstance(document, dict) or set(document) != fields:
        raise ValueError(f'invalid {kind} document fields')
    if type(document['version']) is not int or document['version'] != 1:
        raise ValueError(f'unsupported {kind} document version')
    if document['kind'] != kind:
        raise ValueError('document kind does not match the expected fixed record')
    if not isinstance(document['digest'], str) or document['digest'] != _digest(document):
        raise ValueError(f'{kind} document digest does not match')
    for key in ('voucher_id', 'seal_id', 'fingerprint', 'lot_id', 'station_id'):
        if key in document:
            require_label(document[key], key)
    if 'request_id' in document:
        require_label(document['request_id'], 'request')
    return document
