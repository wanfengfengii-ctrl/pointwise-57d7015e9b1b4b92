from assessment.accept import accept
from assessment.accepted_batches import AcceptedBatchStore, initialize_accepted_batches
from intake.source_book import LotBook, initialize_lot_book
from mixing.operations import initialize_operations, read_revision_operations
from publication.history import PublicationHistory, initialize_publications
from settlement.ledger import initialize_settlement
from settlement.posting import post
from settlement.reconcile import check_mass_equation, retrieve

from tests.support import BATCH, WarehouseCase, assert_rows, fixed, mix


class HistoryTests(WarehouseCase):
    def test_original_receipt_after_correction(self):
        mix(self.source)
        old_issued = fixed(self.source)
        old = accept(self.center, old_issued.data)
        first = post(self.center, 'old', old.seal_id, 'A', ['X', 'Y'])
        mix(self.source, a=7, b=3, revision='r2')
        new_issued = fixed(self.source, 'r2')
        newer = accept(self.center, new_issued.data)
        self.assertNotEqual(newer.seal_id, old.seal_id)
        self.assertEqual(LotBook(self.source).get_revision('A', 'r1').kilograms, 6)
        self.assertEqual(LotBook(self.source).get_revision('A', 'r2').kilograms, 7)
        self.assertEqual(fixed(self.source), old_issued)
        self.assertEqual(PublicationHistory(self.source).get_issued(old.seal_id), old_issued)
        self.source.close()
        self.reopen_center()
        receipt = first.posting.receipt
        self.assertEqual(retrieve(self.center, 'old'), first.posting)
        self.assertEqual(retrieve(self.center, receipt.bill_id, by='bill'), first.posting)
        self.assertEqual((receipt.fingerprint, receipt.revision), (old.fingerprint, 'r1'))
        self.assertEqual((receipt.original_kg, receipt.deducted_kg, receipt.balance_kg), (10, 6, 4))
        self.assertEqual(post(self.center, 'old', old.seal_id, 'A', ['Y', 'X']), first)
        for seal_id, source, selection in (
                (newer.seal_id, 'A', ['X', 'Y']), (old.seal_id, 'B', ['X', 'Y']),
                (old.seal_id, 'A', ['X'])):
            with self.assertRaisesRegex(ValueError, 'conflicting request'):
                post(self.center, 'old', seal_id, source, selection)
        result = post(self.center, 'new', newer.seal_id, 'A', ['X', 'Y'])
        self.assertEqual((result.posting.receipt.deducted_kg, result.posting.receipt.balance_kg), (7, 3))
        self.assertEqual(retrieve(self.center, 'old'), first.posting)
        assert_rows(self, self.center, postings=2, posting_receipts=2, accepted_batches=2)

    def test_initialization_preserves_ledger(self):
        mix(self.source, zero=True)
        issued = fixed(self.source)
        accepted = accept(self.center, issued.data)
        result = post(self.center, 'signed', accepted.seal_id, 'A', ['W', 'X', 'Y'])
        initialize_lot_book(self.source)
        initialize_operations(self.source)
        initialize_publications(self.source)
        initialize_accepted_batches(self.center)
        initialize_settlement(self.center)
        self.source.close()
        source = self.open_source(self.root / 'source.db')
        self.assertEqual(LotBook(source).get_revision('A', 'r1').receipt_id, 'scale-ticket-A-r1')
        self.assertEqual(len(read_revision_operations(source, BATCH, 'r1')), 1)
        self.assertEqual(fixed(source), issued)
        assert_rows(self, source, lot_revisions=2, measured_operations=1, issued_batches=1)
        source.close()
        self.reopen_center()
        self.assertEqual(AcceptedBatchStore(self.center).get(accepted.seal_id), accepted)
        self.assertEqual(retrieve(self.center, 'signed'), result.posting)
        self.assertEqual(post(self.center, 'signed', accepted.seal_id, 'A', ['Y', 'X', 'W']), result)
        assert_rows(self, self.center, request_bindings=1, postings=1, posting_buckets=1, posting_receipts=1)

    def test_mass_equation_and_unknown_lookup(self):
        check_mass_equation(10, 6, 4)
        with self.assertRaisesRegex(ValueError, 'original mass'):
            check_mass_equation(10, 6, 5)
        with self.assertRaises(KeyError):
            retrieve(self.center, 'unknown', by='bill')
