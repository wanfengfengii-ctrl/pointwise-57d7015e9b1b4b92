import hashlib
import json
import sqlite3

from assessment.accept import accept
from assessment.accepted_batches import AcceptedBatchStore
from settlement.posting import post
from settlement.reconcile import retrieve

from tests.support import WarehouseCase, assert_rows, fixed, load, lot, mix, receive, transfer


class OfflineSettlementTests(WarehouseCase):
    def mixed_offline(self, *, zero=False):
        mix(self.source, zero=zero)
        accepted = accept(self.center, fixed(self.source).data)
        self.source.close()
        return accepted.seal_id

    def test_group_is_fixed_buckets_are_not(self):
        seal_id = self.mixed_offline()
        result = post(self.center, 'group', seal_id, 'A', ['Y', 'X'])
        self.assertEqual(result.status, 'posted', 'GROUP_ONLY_DEDUCTION')
        self.assertEqual(result.decision.bounds.group, (6, 6), 'GROUP_ONLY_DEDUCTION')
        self.assertEqual(result.decision.bounds.buckets, (('X', 1, 5), ('Y', 1, 5)))
        actual = retrieve(self.center, 'group')
        receipt = actual.receipt
        self.assertEqual((receipt.seal_id, receipt.original_kg, receipt.deducted_kg, receipt.balance_kg),
                         (seal_id, 10, 6, 4), 'GROUP_ONLY_DEDUCTION')
        self.assertEqual(actual.bucket_deductions, ())
        assert_rows(self, self.center, postings=1, posting_buckets=0, posting_receipts=1)

    def test_single_bucket_pending(self):
        seal_id = self.mixed_offline()
        result = post(self.center, 'single-X', seal_id, 'A', ['X'])
        self.assertEqual(result.status, 'pending')
        self.assertEqual(result.decision.bounds.group, (1, 5))
        self.assertEqual(result.decision.bounds.buckets, (('X', 1, 5),))
        self.assertIsNone(result.posting)
        with self.assertRaises(KeyError):
            retrieve(self.center, 'single-X')
        assert_rows(self, self.center, request_bindings=1, postings=0, posting_buckets=0, posting_receipts=0)

    def test_zero_bucket_is_the_only_proven_detail(self):
        seal_id = self.mixed_offline(zero=True)
        result = post(self.center, 'with-W', seal_id, 'A', ['W', 'X', 'Y'])
        self.assertEqual(result.posting.receipt.deducted_kg, 6)
        self.assertEqual(result.posting.receipt.bucket_bounds, (('W', 0, 0), ('X', 1, 5), ('Y', 1, 5)))
        self.assertEqual(retrieve(self.center, 'with-W').bucket_deductions, (('W', 0),))
        assert_rows(self, self.center, postings=1, posting_buckets=1, posting_receipts=1)

    def test_fixed_bucket_does_not_post_when_group_is_pending(self):
        seal_id = self.mixed_offline(zero=True)
        result = post(self.center, 'pending-with-W', seal_id, 'A', ['X', 'W'])
        self.assertEqual(result.status, 'pending')
        self.assertEqual(result.decision.bounds.group, (1, 5))
        self.assertEqual(result.decision.bounds.buckets, (('W', 0, 0), ('X', 1, 5)))
        self.assertEqual(result.decision.bucket_deductions, ())
        assert_rows(self, self.center, postings=0, posting_buckets=0, posting_receipts=0)

    def test_pending_replay_and_conflict_survive_reopen(self):
        seal_id = self.mixed_offline(zero=True)
        first = post(self.center, 'pending', seal_id, 'A', ['X', 'W'])
        self.reopen_center()
        self.assertEqual(post(self.center, 'pending', seal_id, 'A', ['W', 'X']), first)
        with self.assertRaisesRegex(ValueError, 'conflicting request'):
            post(self.center, 'pending', seal_id, 'A', ['X', 'Y'])
        assert_rows(self, self.center, request_bindings=1, postings=0, posting_receipts=0)

    def test_invalid_selection_leaves_no_binding(self):
        mix(self.source)
        transfer(self.source, 'rejoin', {load('X'): 5, load('Y'): 5}, {'Z': 10})
        seal_id = accept(self.center, fixed(self.source).data).seal_id
        self.source.close()
        for tag, source, selection in (
                ('empty', 'A', []), ('duplicate', 'A', ['Z', 'Z']),
                ('unknown', 'A', ['unseen']), ('consumed', 'A', ['X']), ('lot', 'C', ['Z'])):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                post(self.center, tag, seal_id, source, selection)
        with self.assertRaises(KeyError):
            post(self.center, 'unknown-package', 'absent', 'A', ['Z'])
        assert_rows(self, self.center, request_bindings=0, postings=0, posting_buckets=0, posting_receipts=0)

    def test_rejoin_preserves_source_total(self):
        mix(self.source)
        transfer(self.source, 'rejoin', {load('X'): 5, load('Y'): 5}, {'Z': 10})
        seal_id = accept(self.center, fixed(self.source).data).seal_id
        self.source.close()
        result = post(self.center, 'rejoined', seal_id, 'A', ['Z'])
        self.assertEqual(result.decision.bounds.group, (6, 6))
        self.assertEqual(result.posting.bucket_deductions, (('Z', 6),))
        self.assertEqual(result.posting.receipt.balance_kg, 4)

    def test_cross_transfer_stages_and_order_invariance(self):
        def prepare(db, reverse=False):
            for revision, stage in (('stage-X-Y', 0), ('stage-P-Q', 1), ('stage-Z', 2)):
                mix(db, revision=revision, reverse=reverse)
                if stage >= 1:
                    splits = [
                        ('decant-X', {load('X'): 5}, {'X2': 2, 'X3': 3}),
                        ('decant-Y', {load('Y'): 5}, {'Y2': 2, 'Y3': 3}),
                    ]
                    merges = [
                        ('merge-P', {load('X2'): 2, load('Y3'): 3}, {'P': 5}),
                        ('merge-Q', {load('X3'): 3, load('Y2'): 2}, {'Q': 5}),
                    ]
                    for operations in (splits, merges):
                        for name, inputs, outputs in reversed(operations) if reverse else operations:
                            transfer(db, name, inputs, outputs, revision)
                if stage == 2:
                    transfer(db, 'final', {load('P'): 5, load('Q'): 5}, {'Z': 10}, revision)
            return tuple(fixed(db, name) for name in ('stage-X-Y', 'stage-P-Q', 'stage-Z'))

        other = self.open_source(self.root / 'equivalent.db')
        issued = prepare(self.source)
        self.assertEqual(prepare(other, reverse=True), issued)
        first, cross, final = (accept(self.center, package.data).seal_id for package in issued)
        self.source.close()
        other.close()
        self.assertEqual(post(self.center, 'early', first, 'A', ['X']).decision.bounds.group, (1, 5))
        self.assertEqual(post(self.center, 'isolated-P', cross, 'A', ['P']).decision.bounds.group, (1, 5))
        group = post(self.center, 'P-Q', cross, 'A', ['Q', 'P'])
        self.assertEqual(group.decision.bounds.buckets, (('P', 1, 5), ('Q', 1, 5)))
        self.assertEqual(group.posting.receipt.deducted_kg, 6)
        self.assertEqual(group.posting.bucket_deductions, ())
        self.assertEqual(post(self.center, 'final-Z', final, 'A', ['Z']).posting.bucket_deductions,
                         (('Z', 6),))

    def test_same_bucket_bounds_different_group(self):
        receive(self.source, {'A': 6, 'B': 8}, 'fixed')
        transfer(self.source, 'divide-B', {lot('B', 'fixed'): 8}, {'mix-B': 4, 'Z': 4}, 'fixed')
        transfer(self.source, 'mix', {lot('A', 'fixed'): 6, load('mix-B'): 4}, {'X': 5, 'Y': 5}, 'fixed')
        receive(self.source, {'A': 6, 'B': 8}, 'pending')
        transfer(self.source, 'divide-A', {lot('A', 'pending'): 6}, {'AX': 1, 'AY': 1, 'AR': 4}, 'pending')
        transfer(self.source, 'mix', {load('AR'): 4, lot('B', 'pending'): 8},
                 {'MX': 4, 'MY': 4, 'Z': 4}, 'pending')
        transfer(self.source, 'bucket-X', {load('AX'): 1, load('MX'): 4}, {'X': 5}, 'pending')
        transfer(self.source, 'bucket-Y', {load('AY'): 1, load('MY'): 4}, {'Y': 5}, 'pending')
        first = accept(self.center, fixed(self.source, 'fixed').data)
        second = accept(self.center, fixed(self.source, 'pending').data)
        self.assertEqual(first.package.buckets, second.package.buckets)
        self.source.close()
        one = post(self.center, 'first', first.seal_id, 'A', ['X', 'Y'])
        two = post(self.center, 'second', second.seal_id, 'A', ['X', 'Y'])
        self.assertEqual(one.decision.bounds.buckets, (('X', 1, 5), ('Y', 1, 5)))
        self.assertEqual(two.decision.bounds.buckets, one.decision.bounds.buckets)
        self.assertEqual(one.posting.receipt.deducted_kg, 6)
        self.assertEqual(two.decision.bounds.group, (2, 6))
        self.assertEqual(two.status, 'pending')
        assert_rows(self, self.center, postings=1, posting_receipts=1)

    def test_all_marginals_agree_but_pairing_changes_group(self):
        for revision, split_groups in (
                ('paired', (('X', 'Y'), ('P', 'Q'))),
                ('crossed', (('X', 'P'), ('Y', 'Q')))):
            receive(self.source, {'A': 12, 'B': 8}, revision)
            transfer(self.source, 'pure-A', {lot('A', revision): 12}, {'A1': 6, 'A2': 6}, revision)
            transfer(self.source, 'pure-B', {lot('B', revision): 8}, {'B1': 4, 'B2': 4}, revision)
            for index, pair in enumerate(split_groups, start=1):
                transfer(self.source, f'pair-{index}', {load(f'A{index}'): 6, load(f'B{index}'): 4},
                         dict.fromkeys(pair, 5), revision)
        paired, crossed = (accept(self.center, fixed(self.source, revision).data)
                           for revision in ('paired', 'crossed'))
        self.assertEqual(paired.package.buckets, crossed.package.buckets)
        self.source.close()
        for origin, expected in (('A', (1, 5)), ('B', (0, 4))):
            for name in ('P', 'Q', 'X', 'Y'):
                for package in (paired, crossed):
                    request = f'{package.package.revision}-{origin}-{name}'
                    outcome = post(self.center, request, package.seal_id, origin, [name])
                    self.assertEqual(outcome.decision.bounds.group, expected)
        one = post(self.center, 'pair-XY', paired.seal_id, 'A', ['X', 'Y'])
        two = post(self.center, 'cross-XY', crossed.seal_id, 'A', ['X', 'Y'])
        self.assertEqual((one.status, one.decision.bounds.group), ('posted', (6, 6)))
        self.assertEqual((two.status, two.decision.bounds.group), ('pending', (2, 10)))

    def test_accept_replay_and_conflict_leave_original(self):
        other = self.open_source(self.root / 'conflict.db')
        mix(self.source)
        mix(other, a=5, b=5)
        original = fixed(self.source)
        conflicting = fixed(other)
        first = accept(self.center, original.data)
        self.assertEqual(json.loads(conflicting.data)['seal_id'], first.seal_id)
        reordered = json.loads(original.data)
        reordered['evidence']['sources'].reverse()
        reordered['buckets'].reverse()
        self.assertEqual(accept(self.center, json.dumps(reordered)), first)
        with self.assertRaisesRegex(ValueError, 'conflicting accepted package'):
            accept(self.center, conflicting.data)
        self.source.close()
        other.close()
        self.reopen_center()
        self.assertEqual(AcceptedBatchStore(self.center).get(first.seal_id), first)
        result = post(self.center, 'original', first.seal_id, 'A', ['X', 'Y'])
        self.assertEqual(result.posting.receipt.deducted_kg, 6)
        assert_rows(self, self.center, accepted_batches=1)

    def test_incompatible_evidence_is_rejected_even_with_its_own_digest(self):
        mix(self.source)
        document = json.loads(fixed(self.source).data)
        document['evidence']['sources'][0][2] = 7
        del document['fingerprint']
        canonical = json.dumps(document, sort_keys=True, separators=(',', ':')).encode('ascii')
        document['fingerprint'] = hashlib.sha256(canonical).hexdigest()
        with self.assertRaisesRegex(ValueError, 'incompatible material evidence'):
            accept(self.center, json.dumps(document))
        assert_rows(self, self.center, accepted_batches=0)

    def test_invalid_envelope_or_fingerprint_leaves_no_archive(self):
        mix(self.source)
        original = json.loads(fixed(self.source).data)
        for change in ({'version': True}, {'fingerprint': 'bad'}, {'buckets': [['X', 5]]},
                       {'seal_id': 'different'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                accept(self.center, json.dumps(original | change))
        with self.assertRaises(ValueError):
            accept(self.center, '{"version":1,"version":1}')
        assert_rows(self, self.center, accepted_batches=0)

    def test_commit_failure_and_retry(self):
        seal_id = self.mixed_offline(zero=True)
        self.center.execute('CREATE TABLE guard_parent (id TEXT PRIMARY KEY)')
        self.center.execute('''
            CREATE TABLE guard_child (id TEXT REFERENCES guard_parent(id) DEFERRABLE INITIALLY DEFERRED)
        ''')
        self.center.execute('''
            CREATE TRIGGER fail_receipt_commit AFTER INSERT ON posting_receipts
            BEGIN INSERT INTO guard_child VALUES (NEW.bill_id); END
        ''')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'FOREIGN KEY constraint failed'):
            post(self.center, 'retry', seal_id, 'A', ['W', 'X', 'Y'])
        self.assertFalse(self.center.in_transaction)
        self.reopen_center()
        assert_rows(self, self.center, request_bindings=0, postings=0, posting_buckets=0,
                    posting_receipts=0, guard_child=0, accepted_batches=1)
        with self.assertRaises(KeyError):
            retrieve(self.center, 'retry')
        self.center.execute('DROP TRIGGER fail_receipt_commit')
        result = post(self.center, 'retry', seal_id, 'A', ['Y', 'X', 'W'])
        self.assertEqual(result.posting.receipt.deducted_kg, 6)
        self.assertEqual(result.posting.bucket_deductions, (('W', 0),))
        self.assertEqual(post(self.center, 'retry', seal_id, 'A', ['W', 'X', 'Y']), result)
        assert_rows(self, self.center, request_bindings=1, postings=1, posting_buckets=1, posting_receipts=1)
