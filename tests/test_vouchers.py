import sqlite3

from assessment.accept import accept
from assessment.accepted_batches import initialize_accepted_batches
from examples.voucher_fixture import reject_commits, seed
from intake.source_book import initialize_lot_book
from mixing.assignment import jointly_feasible, minimum_lot_in_group
from mixing.operations import initialize_operations
from publication.history import confirm_return, initialize_publications, issue_voucher
from publication.packages import decode_v1_package
from publication.vouchers import decode_return, decode_voucher, encode_return
from settlement.ledger import initialize_settlement
from settlement.posting import (
    debit_voucher,
    initialize_voucher_station,
    post,
    retrieve_voucher_action,
    return_voucher,
)
from settlement.reconcile import retrieve

from tests.support import WarehouseCase, assert_rows, fixed, lot, mix, receive, transfer


class VoucherCase(WarehouseCase):
    def fixed_package(self):
        if not hasattr(self, '_issued'):
            mix(self.source)
            self._issued = fixed(self.source)
        return self._issued

    def issue(self, voucher_id, station, *, lot_id='A', buckets=('X', 'Y'), kilograms=1,
              connection=None):
        db = connection or self.source
        issued = self.fixed_package()
        return issue_voucher(db, voucher_id, issued.receipt.seal_id, lot_id,
                             list(buckets), station, kilograms)

    def voucher_content(self, connection, voucher_id):
        return connection.execute(
            'SELECT content FROM issued_vouchers WHERE voucher_id = ?', (voucher_id,),
        ).fetchone()[0]

    def open_station_db(self, name, station_id):
        db = sqlite3.connect(self.root / name)
        self.addCleanup(db.close)
        initialize_accepted_batches(db)
        initialize_settlement(db)
        initialize_voucher_station(db, station_id)
        accept(db, self.fixed_package().data)
        return db

    def reopen_source(self):
        self.source.close()
        return self.open_source(self.root / 'source.db')

    def reopen_station(self, name, station_id):
        path = self.root / name
        db = sqlite3.connect(path)
        self.addCleanup(db.close)
        initialize_accepted_batches(db)
        initialize_settlement(db)
        initialize_voucher_station(db, station_id)
        return db


class VoucherIssuingTests(VoucherCase):
    def test_issue_and_replay_keeps_fixed_document(self):
        issued = self.fixed_package()
        first = self.issue('v-p', 'P', buckets=('X',), kilograms=1)
        document = decode_voucher(first)
        self.assertEqual(document['seal_id'], issued.receipt.seal_id)
        self.assertEqual(document['fingerprint'], issued.receipt.fingerprint)
        self.assertEqual((document['lot_id'], document['buckets'], document['station_id'],
                          document['kilograms']),
                         ('A', ('X',), 'P', 1))
        # A reordered bucket list is the same request and yields the same bytes.
        again = issue_voucher(self.source, 'v-p', issued.receipt.seal_id, 'A',
                              ['X'], 'P', 1)
        self.assertEqual(again, first)
        assert_rows(self, self.source, issued_vouchers=1, confirmed_returns=0)

    def test_joint_eligibility_across_all_allocations(self):
        self.issue('v-p', 'P', buckets=('X',), kilograms=1)
        self.issue('v-q', 'Q', buckets=('X', 'Y'), kilograms=5)
        with self.assertRaisesRegex(ValueError, 'jointly'):
            self.issue('v-r', 'R', buckets=('X',), kilograms=1)
        with self.assertRaisesRegex(ValueError, 'jointly'):
            self.issue('v-s', 'S', buckets=('Y',), kilograms=1)

    def test_empty_issue_state_subset_then_second_single_bucket_fails(self):
        self.issue('v-x', 'P', buckets=('X',), kilograms=1)
        self.issue('v-g', 'Q', buckets=('X', 'Y'), kilograms=4)
        with self.assertRaisesRegex(ValueError, 'jointly'):
            self.issue('v-x2', 'R', buckets=('X',), kilograms=1)

    def test_unknown_seal_raises_key_error_and_unknown_parts_value_error(self):
        with self.assertRaises(KeyError):
            issue_voucher(self.source, 'v', 'absent-seal', 'A', ['X'], 'P', 1)
        seal_id = self.fixed_package().receipt.seal_id
        with self.assertRaises(ValueError):
            issue_voucher(self.source, 'v', seal_id, 'A', ['Z'], 'P', 1)
        with self.assertRaises(ValueError):
            issue_voucher(self.source, 'v', seal_id, 'C', ['X'], 'P', 1)
        assert_rows(self, self.source, issued_vouchers=0)

    def test_voucher_id_rebind_is_rejected_but_original_survives(self):
        first = self.issue('v', 'P', buckets=('X',), kilograms=1)
        with self.assertRaisesRegex(ValueError, 'conflicting fixed voucher'):
            self.issue('v', 'Q', buckets=('X',), kilograms=1)
        with self.assertRaisesRegex(ValueError, 'conflicting fixed voucher'):
            self.issue('v', 'P', buckets=('X', 'Y'), kilograms=1)
        with self.assertRaisesRegex(ValueError, 'conflicting fixed voucher'):
            self.issue('v', 'P', buckets=('X',), kilograms=2)
        self.assertEqual(self.issue('v', 'P', buckets=('X',), kilograms=1), first)
        assert_rows(self, self.source, issued_vouchers=1)

    def test_invalid_amounts_identifiers_and_buckets_leave_no_record(self):
        seal_id = self.fixed_package().receipt.seal_id
        for bad in (0, -1, True, False, 1.5, '2'):
            with self.subTest(bad=bad), self.assertRaises((ValueError, TypeError)):
                issue_voucher(self.source, 'v', seal_id, 'A', ['X'], 'P', bad)
        for bad in ('', '  ', 'x\0y'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                issue_voucher(self.source, 'v', seal_id, 'A', ['X'], bad, 1)
        with self.assertRaises(ValueError):
            issue_voucher(self.source, 'v', seal_id, 'A', [], 'P', 1)
        with self.assertRaises(ValueError):
            issue_voucher(self.source, 'v', seal_id, 'A', ['X', 'X'], 'P', 1)
        assert_rows(self, self.source, issued_vouchers=0)

    def test_returns_release_capacity_with_out_of_order_confirmation(self):
        seal_id = self.fixed_package().receipt.seal_id
        p_voucher = self.issue('v-p', 'P', buckets=('X',), kilograms=1)
        q_voucher = self.issue('v-q', 'Q', buckets=('X', 'Y'), kilograms=5)
        q = self.open_station_db('q.db', 'Q')
        debit_voucher(q, q_voucher, 'q-d1', 2)
        r1 = return_voucher(q, q_voucher, 'R1', 1)
        r2 = return_voucher(q, q_voucher, 'R2', 1)
        with self.assertRaises(ValueError):
            issue_voucher(self.source, 'v-t', seal_id, 'A', ['X', 'Y'], 'T', 2)

        reopened = self.reopen_source()
        self.assertEqual(
            confirm_return(reopened, r2),
            {'station_id': 'Q', 'request_id': 'R2', 'voucher_id': 'v-q', 'kilograms': 1},
        )
        # Only one of two returns confirmed: outstanding is still 4, plus 2 > 6.
        with self.assertRaises(ValueError):
            issue_voucher(reopened, 'v-t', seal_id, 'A', ['X', 'Y'], 'T', 2)
        self.assertEqual(confirm_return(reopened, r2)['request_id'], 'R2')
        self.assertEqual(confirm_return(reopened, r1)['kilograms'], 1)
        new_voucher = issue_voucher(reopened, 'v-t', seal_id, 'A', ['X', 'Y'], 'T', 2)
        self.assertEqual(decode_voucher(new_voucher)['kilograms'], 2)
        with self.assertRaises(ValueError):
            issue_voucher(reopened, 'v-u', seal_id, 'A', ['X', 'Y'], 'U', 1)
        assert_rows(self, reopened, issued_vouchers=3, confirmed_returns=2)
        self.assertIsNotNone(p_voucher)

    def test_confirm_rejects_wrong_voucher_station_conflict_and_excess(self):
        self.issue('v-q', 'Q', buckets=('X', 'Y'), kilograms=5)
        q = self.open_station_db('q.db', 'Q')
        voucher = self.voucher_content(self.source, 'v-q')
        good = return_voucher(q, voucher, 'R1', 1)
        document = decode_voucher(voucher)
        forged_unknown = encode_return(
            station_id='Q', request_id='RX', voucher_id='missing', kilograms=1,
            seal_id=document['seal_id'], fingerprint=document['fingerprint'],
            lot_id=document['lot_id'], buckets=document['buckets'])
        with self.assertRaises(ValueError):
            confirm_return(self.source, forged_unknown)
        wrong_station = encode_return(
            station_id='P', request_id='R2', voucher_id='v-q', kilograms=1,
            seal_id=document['seal_id'], fingerprint=document['fingerprint'],
            lot_id=document['lot_id'], buckets=document['buckets'])
        with self.assertRaisesRegex(ValueError, 'issued voucher'):
            confirm_return(self.source, wrong_station)
        # The valid document confirms once and replays its own fixed result.
        self.assertEqual(confirm_return(self.source, good)['request_id'], 'R1')
        self.assertEqual(confirm_return(self.source, good)['kilograms'], 1)
        # A further document would push confirmed mass past the voucher amount.
        oversized = encode_return(
            station_id='Q', request_id='R9', voucher_id='v-q', kilograms=5,
            seal_id=document['seal_id'], fingerprint=document['fingerprint'],
            lot_id=document['lot_id'], buckets=document['buckets'])
        with self.assertRaisesRegex(ValueError, 'exceed'):
            confirm_return(self.source, oversized)
        # Replaying R1 with different content is an identity conflict.
        rebound = encode_return(
            station_id='Q', request_id='R1', voucher_id='v-q', kilograms=1,
            seal_id=document['seal_id'], fingerprint=document['fingerprint'],
            lot_id=document['lot_id'], buckets=('Y',))
        with self.assertRaisesRegex(ValueError, 'identity conflicts'):
            confirm_return(self.source, rebound)
        assert_rows(self, self.source, confirmed_returns=1)

    def test_failed_confirm_commit_releases_nothing(self):
        seal_id = self.fixed_package().receipt.seal_id
        voucher = self.issue('v-q', 'Q', buckets=('X', 'Y'), kilograms=5)
        q = self.open_station_db('q.db', 'Q')
        returned = return_voucher(q, voucher, 'R1', 1)
        reopened = self.reopen_source()
        with reject_commits(reopened):
            with self.assertRaises(sqlite3.DatabaseError):
                confirm_return(reopened, returned)
        self.assertFalse(reopened.in_transaction)
        assert_rows(self, reopened, confirmed_returns=0)
        with self.assertRaises(ValueError):
            issue_voucher(reopened, 'v-t', seal_id, 'A', ['X', 'Y'], 'T', 2)
        self.assertEqual(confirm_return(reopened, returned)['kilograms'], 1)
        self.assertEqual(confirm_return(reopened, returned)['request_id'], 'R1')
        assert_rows(self, reopened, confirmed_returns=1)


class VoucherStationTests(VoucherCase):
    def test_debit_and_return_share_request_space_and_update_availability(self):
        voucher = self.issue('v-q', 'Q', buckets=('X', 'Y'), kilograms=5)
        q = self.open_station_db('q.db', 'Q')
        first = debit_voucher(q, voucher, 'd1', 2)
        self.assertEqual((first['station_id'], first['voucher_id'], first['buckets'],
                          first['kilograms'], first['lot_id']),
                         ('Q', 'v-q', ('X', 'Y'), 2, 'A'))
        self.assertEqual(first['seal_id'], self.fixed_package().receipt.seal_id)
        self.assertEqual(first['fingerprint'], self.fixed_package().receipt.fingerprint)
        self.assertEqual(debit_voucher(q, voucher, 'd1', 2), first)
        returned = return_voucher(q, voucher, 'R1', 1)
        document = decode_return(returned)
        self.assertEqual((document['station_id'], document['request_id'],
                          document['voucher_id'], document['kilograms']),
                         ('Q', 'R1', 'v-q', 1))
        with self.assertRaisesRegex(ValueError, 'still available'):
            debit_voucher(q, voucher, 'd2', 3)
        debit_voucher(q, voucher, 'd2', 1)
        return_voucher(q, voucher, 'R2', 1)
        with self.assertRaisesRegex(ValueError, 'still available'):
            return_voucher(q, voucher, 'R3', 1)
        self.assertEqual(retrieve_voucher_action(q, 'd1'), first)
        self.assertEqual(retrieve_voucher_action(q, 'R1'), returned)
        with self.assertRaises(KeyError):
            retrieve_voucher_action(q, 'unknown')
        assert_rows(self, q, voucher_actions=4, station_vouchers=1)

    def test_replay_rebind_and_cross_kind_requests(self):
        voucher = self.issue('v-q', 'Q', buckets=('X', 'Y'), kilograms=5)
        q = self.open_station_db('q.db', 'Q')
        debit_voucher(q, voucher, 'shared', 1)
        with self.assertRaisesRegex(ValueError, 'different action kind'):
            return_voucher(q, voucher, 'shared', 1)
        with self.assertRaisesRegex(ValueError, 'different parameters'):
            debit_voucher(q, voucher, 'shared', 2)
        other = self.issue('v-p', 'P', buckets=('X',), kilograms=1)
        with self.assertRaisesRegex(ValueError, 'different station'):
            debit_voucher(q, other, 'p-at-q', 1)

    def test_vouchers_belong_to_their_named_station(self):
        # Two vouchers on the same seal, swapped between their target stations.
        p_voucher = self.issue('v-p', 'P', buckets=('X',), kilograms=1)
        q_voucher = self.issue('v-q', 'Q', buckets=('X', 'Y'), kilograms=2)
        p = self.open_station_db('p.db', 'P')
        q = self.open_station_db('q.db', 'Q')
        with self.assertRaisesRegex(ValueError, 'different station'):
            debit_voucher(p, q_voucher, 'swap-1', 1)
        with self.assertRaisesRegex(ValueError, 'different station'):
            return_voucher(q, p_voucher, 'swap-2', 1)
        # Neither rejected delivery changed local availability.
        self.assertEqual(debit_voucher(p, p_voucher, 'pd', 1)['kilograms'], 1)
        self.assertEqual(debit_voucher(q, q_voucher, 'qd', 2)['kilograms'], 2)
        assert_rows(self, p, voucher_actions=1)
        assert_rows(self, q, voucher_actions=1)

    def test_independent_trials_keep_their_original_group_receipt(self):
        # From a fresh issue state each trial promises 5 kg over X+Y, debits it,
        # then proves a further 1 kg over only X or only Y is still issuable.
        for group in (('X',), ('Y',)):
            source_db = sqlite3.connect(self.root / f'source-{group[0]}.db')
            self.addCleanup(source_db.close)
            initialize_lot_book(source_db)
            initialize_operations(source_db)
            initialize_publications(source_db)
            issued = seed(source_db)
            seal_id = issued.receipt.seal_id
            five = issue_voucher(source_db, 'v5', seal_id, 'A', ['X', 'Y'], 'Q', 5)
            station_db = sqlite3.connect(self.root / f'q-{group[0]}.db')
            self.addCleanup(station_db.close)
            initialize_accepted_batches(station_db)
            initialize_settlement(station_db)
            initialize_voucher_station(station_db, 'Q')
            accept(station_db, issued.data)
            original = debit_voucher(station_db, five, 'd5', 5)
            source_db.close()
            with sqlite3.connect(self.root / f'source-{group[0]}.db') as reopened:
                extra = issue_voucher(reopened, 'v1', seal_id, 'A', list(group), 'P', 1)
                self.assertEqual(decode_voucher(extra)['buckets'], group)
                # Replaying the five-kilogram request keeps its first receipt.
            self.assertEqual(
                debit_voucher(station_db, five, 'd5', 5), original)


    def test_wrong_station_missing_batch_and_revision_fingerprint(self):
        voucher = self.issue('v-q', 'Q', buckets=('X', 'Y'), kilograms=2)
        unbound = sqlite3.connect(self.root / 'unbound.db')
        self.addCleanup(unbound.close)
        initialize_accepted_batches(unbound)
        initialize_settlement(unbound)
        with self.assertRaisesRegex(ValueError, 'not bound'):
            debit_voucher(unbound, voucher, 'd', 1)
        p = self.open_station_db('p.db', 'P')
        with self.assertRaisesRegex(ValueError, 'different station'):
            debit_voucher(p, voucher, 'd', 1)
        # A station holding only another same-valued revision lacks this batch.
        other_path = self.root / 'other-source.db'
        with sqlite3.connect(other_path) as other_db:
            initialize_lot_book(other_db)
            initialize_operations(other_db)
            initialize_publications(other_db)
            other_issued = seed(other_db, revision='r2')
        r2 = sqlite3.connect(self.root / 'r2.db')
        self.addCleanup(r2.close)
        initialize_accepted_batches(r2)
        initialize_settlement(r2)
        initialize_voucher_station(r2, 'Q')
        accept(r2, other_issued.data)
        with self.assertRaisesRegex(ValueError, 'no accepted sealed batch'):
            debit_voucher(r2, voucher, 'd', 1)
        accept(r2, self.fixed_package().data)
        self.assertEqual(debit_voucher(r2, voucher, 'd', 1)['kilograms'], 1)

    def test_rebind_station_identity_is_rejected_but_reopen_is_idempotent(self):
        db = sqlite3.connect(self.root / 'bound.db')
        self.addCleanup(db.close)
        initialize_accepted_batches(db)
        initialize_settlement(db)
        initialize_voucher_station(db, 'P')
        initialize_voucher_station(db, 'P')
        with self.assertRaisesRegex(ValueError, 'already bound'):
            initialize_voucher_station(db, 'Q')
        # The original station binding is untouched.
        self.assertEqual(db.execute('SELECT station_id FROM voucher_station').fetchone()[0], 'P')

    def test_failed_station_commit_keeps_availability_and_has_no_document(self):
        voucher = self.issue('v-q', 'Q', buckets=('X', 'Y'), kilograms=5)
        q = self.open_station_db('q.db', 'Q')
        with reject_commits(q):
            with self.assertRaises(sqlite3.DatabaseError):
                return_voucher(q, voucher, 'R1', 2)
        self.assertFalse(q.in_transaction)
        with self.assertRaises(KeyError):
            retrieve_voucher_action(q, 'R1')
        assert_rows(self, q, voucher_actions=0, station_vouchers=0)
        debit_voucher(q, voucher, 'R1', 5)
        with self.assertRaises(ValueError):
            debit_voucher(q, voucher, 'd2', 1)

    def test_history_survives_reopen_and_three_stations_use_six_kg(self):
        seal_id = self.fixed_package().receipt.seal_id
        v_p = self.issue('v-p', 'P', buckets=('X',), kilograms=1)
        v_q = self.issue('v-q', 'Q', buckets=('X', 'Y'), kilograms=5)
        q = self.open_station_db('q.db', 'Q')
        debit_voucher(q, v_q, 'd1', 2)
        r1 = return_voucher(q, v_q, 'R1', 1)
        r2 = return_voucher(q, v_q, 'R2', 1)
        q.close()
        reopened = self.reopen_source()
        confirm_return(reopened, r2)
        confirm_return(reopened, r1)
        v_t = issue_voucher(reopened, 'v-t', seal_id, 'A', ['X', 'Y'], 'T', 2)
        with self.assertRaises(ValueError):
            issue_voucher(reopened, 'v-u', seal_id, 'A', ['X', 'Y'], 'U', 1)

        q2 = self.reopen_station('q.db', 'Q')
        debit_voucher(q2, v_q, 'd2', 1)
        self.assertEqual(retrieve_voucher_action(q2, 'd1')['kilograms'], 2)
        self.assertEqual(retrieve_voucher_action(q2, 'd2')['kilograms'], 1)
        self.assertEqual(decode_return(retrieve_voucher_action(q2, 'R1'))['kilograms'], 1)
        self.assertEqual(decode_return(retrieve_voucher_action(q2, 'R2'))['kilograms'], 1)
        with self.assertRaises(ValueError):
            debit_voucher(q2, v_q, 'd3', 1)

        p = self.open_station_db('p.db', 'P')
        t = self.open_station_db('t.db', 'T')
        debit_voucher(p, v_p, 'pd', 1)
        debit_voucher(t, v_t, 'td', 2)

        def debit_total(path):
            with sqlite3.connect(path) as db:
                return int(db.execute(
                    "SELECT COALESCE(SUM(kilograms), 0) FROM voucher_actions "
                    "WHERE kind = 'debit'").fetchone()[0])

        total = (debit_total(self.root / 'q.db')
                 + debit_total(self.root / 'p.db')
                 + debit_total(self.root / 't.db'))
        self.assertEqual(total, 6)

    def test_legacy_posting_still_works_alongside_vouchers(self):
        seal_id = self.fixed_package().receipt.seal_id
        voucher = self.issue('v-q', 'Q', buckets=('X', 'Y'), kilograms=5)
        center = self.open_station_db('center.db', 'Q')
        legacy = post(center, 'legacy-group', seal_id, 'A', ['Y', 'X'])
        self.assertEqual(legacy.status, 'posted')
        self.assertEqual(legacy.posting.receipt.deducted_kg, 6)
        debit_voucher(center, voucher, 'v-d', 2)
        self.assertEqual(retrieve(center, 'legacy-group'), legacy.posting)
        self.assertEqual(retrieve_voucher_action(center, 'v-d')['kilograms'], 2)

    def test_malformed_documents_are_rejected_without_state_change(self):
        self.issue('v-q', 'Q', buckets=('X', 'Y'), kilograms=2)
        q = self.open_station_db('q.db', 'Q')
        for bad in (b'not json', b'{"kind":"voucher"}', b'{"version":1,"kind":"voucher"}',
                    123, None):
            with self.subTest(bad=bad), self.assertRaises((ValueError, TypeError)):
                debit_voucher(q, bad, 'd', 1)
        assert_rows(self, q, voucher_actions=0, station_vouchers=0)

    def test_single_source_split_keeps_group_level_receipt(self):
        receive(self.source, {'A': 10})
        transfer(self.source, 'split', {lot('A'): 10}, {'X': 10, 'Y': 0})
        self._issued = fixed(self.source)
        voucher = self.issue('v', 'Q', buckets=('X', 'Y'), kilograms=10)
        q = self.open_station_db('zero.db', 'Q')
        receipt = debit_voucher(q, voucher, 'd', 10)
        self.assertEqual((receipt['buckets'], receipt['kilograms']), (('X', 'Y'), 10))
        with self.assertRaises(ValueError):
            debit_voucher(q, voucher, 'd2', 1)


class AssignmentFeasibilityTests(VoucherCase):
    def test_guaranteed_minimum_matches_known_bounds(self):
        evidence = decode_v1_package(self.fixed_package().data).evidence
        self.assertEqual(minimum_lot_in_group(evidence, 'A', ('X', 'Y')), 6)
        self.assertEqual(minimum_lot_in_group(evidence, 'A', ('X',)), 1)
        self.assertEqual(minimum_lot_in_group(evidence, 'A', ('Y',)), 1)

    def test_hall_conditions_on_the_seed_network(self):
        evidence = decode_v1_package(self.fixed_package().data).evidence
        self.assertTrue(jointly_feasible(
            evidence, 'A', [(('X',), 1), (('X', 'Y'), 5)]))
        self.assertFalse(jointly_feasible(
            evidence, 'A', [(('X',), 1), (('X', 'Y'), 5), (('X',), 1)]))
        self.assertTrue(jointly_feasible(
            evidence, 'A', [(('X',), 1), (('Y',), 1), (('X', 'Y'), 4)]))
        self.assertFalse(jointly_feasible(
            evidence, 'A', [(('X',), 5), (('Y',), 2)]))
        with self.assertRaises(ValueError):
            jointly_feasible(evidence, 'A', [(('Z',), 1)])
