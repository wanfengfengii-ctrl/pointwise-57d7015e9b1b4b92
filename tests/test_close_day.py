from assessment.accept import accept
from intake.lots import record_lot
from intake.source_book import LotBook
from mixing.operations import read_revision_operations
from publication.history import PublicationHistory
from settlement.posting import post

from tests.support import BATCH, WarehouseCase, assert_rows, fixed, load, lot, receive, transfer


class CloseDayTests(WarehouseCase):
    def test_single_source_split(self):
        receive(self.source, {'A': 10})
        operation = transfer(self.source, 'split', {lot('A'): 10}, {'X': 4, 'Y': 6})
        self.assertEqual(operation.state, 'ready')
        issued = fixed(self.source)
        self.assertEqual(PublicationHistory(self.source).get_issued(issued.receipt.seal_id), issued)
        self.assertNotIn(b'scale-ticket', issued.data)
        self.assertNotIn(b'split', issued.data)
        accepted = accept(self.center, issued.data)
        self.assertEqual(accepted.fingerprint, issued.receipt.fingerprint)
        self.source.close()
        result = post(self.center, 'single-source', accepted.seal_id, 'A', ['Y', 'X'])
        self.assertEqual(result.status, 'posted')
        self.assertEqual(result.decision.bounds.group, (10, 10))
        self.assertEqual(result.decision.bounds.buckets, (('X', 4, 4), ('Y', 6, 6)))
        self.assertEqual(result.posting.bucket_deductions, (('X', 4), ('Y', 6)))

    def test_same_revision_conflict(self):
        original = record_lot(self.source, 'A', 'r1', 10, 'ticket-10')
        self.assertEqual(record_lot(self.source, 'A', 'r1', 10, 'ticket-10'), original)
        for kg, receipt in ((11, 'ticket-10'), (10, 'different-ticket')):
            with self.assertRaisesRegex(ValueError, 'conflicting source revision'):
                record_lot(self.source, 'A', 'r1', kg, receipt)
        self.source.close()
        source = self.open_source(self.root / 'source.db')
        self.assertEqual(LotBook(source).get_revision('A', 'r1'), original)
        self.assertEqual(record_lot(source, 'A', 'r2', 11, 'ticket-11').kilograms, 11)
        self.assertEqual(LotBook(source).get_revision('A', 'r1'), original)

    def test_invalid_source_quantity(self):
        for weight in (None, True, False, -1, 1.0, '1'):
            with self.subTest(weight=weight), self.assertRaises(ValueError):
                record_lot(self.source, 'A', 'r1', weight, 'receipt')
        assert_rows(self, self.source, lot_revisions=0)

    def test_overweighed_output(self):
        receive(self.source, {'A': 10})
        with self.assertRaisesRegex(ValueError, 'out-of-balance'):
            transfer(self.source, 'split', {lot('A'): 10}, {'X': 5, 'Y': 6})
        with self.assertRaisesRegex(ValueError, 'no measured operations'):
            fixed(self.source)
        assert_rows(self, self.source, measured_operations=0, issued_batches=0)

    def test_missing_weighing_can_be_completed_to_zero(self):
        receive(self.source, {'A': 10})
        draft = transfer(self.source, 'split', {lot('A'): 10}, {'X': 10, 'Y': None})
        self.assertEqual(draft.state, 'pending_weighings')
        with self.assertRaisesRegex(ValueError, 'missing weighing'):
            fixed(self.source)
        assert_rows(self, self.source, measured_operations=1, issued_batches=0)
        with self.assertRaises(ValueError):
            transfer(self.source, 'split', {lot('A'): 10}, {'X': 10, 'Y': 1})
        self.assertEqual(read_revision_operations(self.source, BATCH, 'r1'), (draft,))
        complete = transfer(self.source, 'split', {lot('A'): None}, {'Y': 0, 'X': None})
        self.assertEqual(complete.state, 'ready')
        accepted = accept(self.center, fixed(self.source).data)
        self.assertEqual(accepted.package.buckets, (('X', 10), ('Y', 0)))
        self.source.close()
        posted = post(self.center, 'zero', accepted.seal_id, 'A', ['Y'])
        self.assertEqual(posted.posting.bucket_deductions, (('Y', 0),))

    def test_zero_receipt_and_zero_operation(self):
        receive(self.source, {'A': 0})
        self.assertEqual(LotBook(self.source).get_revision('A', 'r1').kilograms, 0)
        transfer(self.source, 'zero', {lot('A'): 0}, {'X': 0})
        accepted = accept(self.center, fixed(self.source).data)
        self.source.close()
        result = post(self.center, 'zero-source', accepted.seal_id, 'A', ['X'])
        self.assertEqual(result.status, 'posted')
        self.assertEqual(result.posting.receipt.balance_kg, 0)

    def test_reused_input_is_rejected_by_registration(self):
        receive(self.source, {'A': 10})
        transfer(self.source, 'split', {lot('A'): 10}, {'X': 4, 'Y': 6})
        transfer(self.source, 'to-P', {load('X'): 4}, {'P': 4})
        with self.assertRaisesRegex(ValueError, 'duplicate consumption'):
            transfer(self.source, 'to-Q', {load('X'): 4}, {'Q': 4})
        self.source.close()
        source = self.open_source(self.root / 'source.db')
        self.assertEqual({row.operation_id for row in read_revision_operations(source, BATCH, 'r1')},
                         {'split', 'to-P'})
        accepted = accept(self.center, fixed(source).data)
        self.assertEqual(accepted.package.buckets, (('P', 4), ('Y', 6)))

    def test_unknown_load_duplicate_producer_and_peer_conflict(self):
        receive(self.source, {'A': 10, 'B': 4})
        with self.assertRaisesRegex(ValueError, 'unknown input load'):
            transfer(self.source, 'unknown', {load('missing'): 4}, {'P': 4})
        transfer(self.source, 'split', {lot('A'): 10}, {'X': 4, 'Y': 6})
        with self.assertRaisesRegex(ValueError, 'already has a producer'):
            transfer(self.source, 'other', {lot('B'): 4}, {'X': 4})
        with self.assertRaisesRegex(ValueError, 'conflicting input weighing'):
            transfer(self.source, 'transfer', {load('X'): 5}, {'P': 5})
        assert_rows(self, self.source, measured_operations=1)

    def test_downstream_required_mass_rejects_incompatible_draft(self):
        receive(self.source, {'A': 10})
        transfer(self.source, 'upstream', {lot('A'): 10}, {'X': None, 'Y': 8})
        with self.assertRaisesRegex(ValueError, 'no conserved completion'):
            transfer(self.source, 'downstream', {load('X'): None}, {'Z': 3})
        transfer(self.source, 'downstream', {load('X'): None}, {'Z': 2})
        with self.assertRaisesRegex(ValueError, 'missing weighing'):
            fixed(self.source)
        transfer(self.source, 'upstream', {lot('A'): 10}, {'X': 2, 'Y': 8})
        transfer(self.source, 'downstream', {load('X'): 2}, {'Z': 2})
        accepted = accept(self.center, fixed(self.source).data)
        self.assertEqual(accepted.package.buckets, (('Y', 8), ('Z', 2)))

    def test_seal_blocks_new_operations_but_keeps_original_replay(self):
        receive(self.source, {'A': 10})
        old = transfer(self.source, 'split', {lot('A'): 10}, {'X': 10})
        issued = fixed(self.source)
        self.assertEqual(transfer(self.source, 'split', {lot('A'): 10}, {'X': 10}), old)
        with self.assertRaisesRegex(ValueError, 'sealed revision'):
            transfer(self.source, 'move', {load('X'): 10}, {'Y': 10})
        self.assertEqual(fixed(self.source), issued)
