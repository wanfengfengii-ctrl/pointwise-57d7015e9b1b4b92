from dataclasses import dataclass
from hashlib import sha256


@dataclass(frozen=True)
class PostingReceipt:
    bill_id: str
    request_id: str
    seal_id: str
    fingerprint: str
    batch: str
    revision: str
    lot_id: str
    buckets: tuple[str, ...]
    original_kg: int
    deducted_kg: int
    balance_kg: int
    group_bounds: tuple[int, int]
    bucket_bounds: tuple[tuple[str, int, int], ...]


def make_posting_receipt(accepted, request, decision):
    if decision.deducted_kg is None:
        raise ValueError('a pending assessment cannot have a posting receipt')
    package = accepted.package
    bill_id = 'bill-' + sha256(request.request_id.encode('utf-8')).hexdigest()
    return PostingReceipt(
        bill_id, request.request_id, accepted.seal_id, accepted.fingerprint,
        package.batch, package.revision, request.lot_id, request.buckets,
        decision.bounds.original_kg, decision.deducted_kg, decision.balance_kg,
        decision.bounds.group, decision.bounds.buckets,
    )
