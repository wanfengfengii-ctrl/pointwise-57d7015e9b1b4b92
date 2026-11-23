from dataclasses import dataclass

from assessment.accepted_batches import AcceptedBatchStore
from assessment.bounds import compute_bounds
from assessment.eligibility import AssessmentDecision, decide_eligibility
from assessment.requests import AssessmentRequest, normalize_request, request_identity
from intake.source_book import write_transaction

from .ledger import PostingRecord, SettlementLedger
from .receipts import make_posting_receipt
from .voucher_station import (
    debit_voucher,
    initialize_voucher_station,
    retrieve_voucher_action,
    return_voucher,
)

__all__ = [
    'PostResult',
    'post',
    'initialize_voucher_station',
    'debit_voucher',
    'return_voucher',
    'retrieve_voucher_action',
]


@dataclass(frozen=True)
class PostResult:
    request: AssessmentRequest
    decision: AssessmentDecision
    posting: PostingRecord | None

    @property
    def status(self):
        return self.decision.status


def post(connection, request_id, seal_id, lot_id, buckets):
    request = normalize_request(request_id, seal_id, lot_id, buckets)
    ledger = SettlementLedger(connection)
    with write_transaction(connection):
        previous = ledger.find_request(request.request_id)
        if previous is not None:
            if previous.identity != request_identity(request):
                raise ValueError(f'conflicting request identity {request.request_id}')
            original = ledger.get_posting(previous.bill_id, by='bill') if previous.bill_id is not None else None
            if (original is None) != (previous.decision.status == 'pending'):
                raise ValueError('stored decision disagrees with the ledger')
            return PostResult(request, previous.decision, original)

        accepted = AcceptedBatchStore(connection).get(request.seal_id)
        if accepted is None:
            raise KeyError(request.seal_id)
        decision = decide_eligibility(compute_bounds(accepted, request))
        if decision.status == 'pending':
            ledger.commit_pending(request, decision)
            record = None
        else:
            receipt = make_posting_receipt(accepted, request, decision)
            record = ledger.commit_posting(request, decision, receipt)
    return PostResult(request, decision, record)
