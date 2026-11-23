import json
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from assessment.accept import accept
from assessment.accepted_batches import initialize_accepted_batches
from intake.lots import record_lot
from intake.source_book import initialize_lot_book
from mixing.operations import initialize_operations, record_transfer
from publication.freeze import seal
from publication.history import initialize_publications
from settlement.ledger import initialize_settlement
from settlement.posting import post
from settlement.reconcile import retrieve


def main():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        center_path = root / 'center.db'
        with closing(sqlite3.connect(center_path)) as center:
            initialize_accepted_batches(center)
            initialize_settlement(center)
            with closing(sqlite3.connect(root / 'source.db')) as source:
                initialize_lot_book(source)
                initialize_operations(source)
                initialize_publications(source)
                record_lot(source, 'A', 'r1', 6, 'original-A')
                record_lot(source, 'B', 'r1', 4, 'original-B')
                record_transfer(source, 'demo-day', 'r1', 'mix-and-fill',
                                {('lot', 'A', 'r1'): 6, ('lot', 'B', 'r1'): 4}, {'X': 5, 'Y': 5})
                issued = seal(source, 'demo-day', 'r1')
                accepted = accept(center, issued.data)
            group = post(center, 'demo-group', accepted.seal_id, 'A', ['Y', 'X'])
            pending = post(center, 'demo-single', accepted.seal_id, 'A', ['X'])
        with closing(sqlite3.connect(center_path)) as center:
            initialize_accepted_batches(center)
            initialize_settlement(center)
            old = retrieve(center, 'demo-group')
            pending_has_ledger = center.execute(
                'SELECT 1 FROM postings WHERE request_id = ?', ('demo-single',),
            ).fetchone() is not None

    report = {
        'case_id': 'group_only',
        'input': {'lots_kg': {'A': 6, 'B': 4}, 'buckets_kg': {'X': 5, 'Y': 5}, 'remove': 'A'},
        'expected': {'status': 'posted', 'group': (6, 6), 'buckets': (('X', 1, 5), ('Y', 1, 5))},
        'actual': {'status': group.status, 'group': group.decision.bounds.group,
                   'buckets': group.decision.bounds.buckets},
        'downstream_expected': {'original_kg': 10, 'deducted_kg': 6, 'balance_kg': 4,
                                'bucket_deductions': (), 'original_receipt_retrieved': True},
        'downstream_actual': {'original_kg': old.receipt.original_kg, 'deducted_kg': old.receipt.deducted_kg,
                              'balance_kg': old.receipt.balance_kg, 'bucket_deductions': old.bucket_deductions,
                              'original_receipt_retrieved': old == group.posting and
                              old.receipt.seal_id == issued.receipt.seal_id},
        'code_paths': ['publication/freeze.py', 'assessment/accept.py', 'assessment/bounds.py',
                       'settlement/posting.py', 'settlement/reconcile.py'],
        'oracle': 'All 6 kg from A must be in X or Y. One 5 kg bucket can receive at most 4 kg from B.',
    }
    if report['actual'] != report['expected'] or report['downstream_actual'] != report['downstream_expected']:
        raise AssertionError(f'group deduction does not reconcile: {report}')
    print('POINTWISE_SCENARIO ' + json.dumps(report, sort_keys=True))

    report = {
        'case_id': 'single_bucket_pending',
        'input': {'lots_kg': {'A': 6, 'B': 4}, 'selection': ['X'], 'remove': 'A'},
        'expected': {'status': 'pending', 'group': (1, 5)},
        'actual': {'status': pending.status, 'group': pending.decision.bounds.group},
        'downstream_expected': {'posting': False, 'success_receipt': False},
        'downstream_actual': {'posting': pending_has_ledger,
                              'success_receipt': pending.posting is not None},
        'code_paths': ['assessment/bounds.py', 'assessment/eligibility.py', 'settlement/posting.py'],
        'oracle': 'X can contain 1 kg A with 4 kg B, or 5 kg A with 0 kg B.',
    }
    if report['actual'] != report['expected'] or report['downstream_actual'] != report['downstream_expected']:
        raise AssertionError(f'single-bucket assessment does not reconcile: {report}')
    print('POINTWISE_SCENARIO ' + json.dumps(report, sort_keys=True))


if __name__ == '__main__':
    main()
