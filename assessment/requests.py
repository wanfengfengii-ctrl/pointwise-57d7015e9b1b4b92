import json
from dataclasses import dataclass

from intake.weighings import require_label

from .accepted_batches import AcceptedBatch


@dataclass(frozen=True)
class AssessmentRequest:
    request_id: str
    seal_id: str
    lot_id: str
    buckets: tuple[str, ...]


def normalize_request(request_id, seal_id, lot_id, buckets):
    request_id = require_label(request_id, 'request')
    seal_id = require_label(seal_id, 'sealed batch')
    lot_id = require_label(lot_id, 'source lot')
    if not isinstance(buckets, (list, tuple)) or not buckets:
        raise ValueError('selection requires a nonempty list or tuple of terminal buckets')
    names = tuple(require_label(name, 'bucket') for name in buckets)
    if len(set(names)) != len(names):
        raise ValueError('selection contains a duplicate bucket')
    return AssessmentRequest(request_id, seal_id, lot_id, tuple(sorted(names)))


def validate_selection(request, accepted: AcceptedBatch):
    if request.seal_id != accepted.seal_id:
        raise ValueError('request refers to a different fixed package')
    if request.lot_id not in {name for name, _, _ in accepted.package.evidence.sources}:
        raise ValueError(f'unknown source lot {request.lot_id}')
    available = {name for name, _ in accepted.package.buckets}
    for name in request.buckets:
        if name not in available:
            raise ValueError(f'unknown terminal bucket {name}')


def request_identity(request):
    return json.dumps([request.seal_id, request.lot_id, request.buckets], separators=(',', ':'))
