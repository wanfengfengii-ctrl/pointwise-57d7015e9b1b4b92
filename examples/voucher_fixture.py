from argparse import ArgumentParser
from contextlib import closing, contextmanager
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

from assessment.accept import accept
from assessment.accepted_batches import initialize_accepted_batches
from intake.lots import record_lot
from intake.source_book import initialize_lot_book
from mixing.operations import initialize_operations, record_transfer
from publication.freeze import seal
from publication.history import confirm_return, initialize_publications, issue_voucher
from settlement.ledger import initialize_settlement
from settlement.posting import (
    debit_voucher,
    initialize_voucher_station,
    post,
    retrieve_voucher_action,
    return_voucher,
)
from settlement.reconcile import retrieve


@contextmanager
def open_source(path):
    with closing(sqlite3.connect(path)) as db:
        initialize_lot_book(db)
        initialize_operations(db)
        initialize_publications(db)
        yield db


@contextmanager
def open_station(path, station_id=None):
    with closing(sqlite3.connect(path)) as db:
        initialize_accepted_batches(db)
        initialize_settlement(db)
        if station_id is not None:
            initialize_voucher_station(db, station_id)
        yield db


def seed(db, revision="r1", *, a=6, b=4, buckets=None):
    if buckets is None:
        buckets = {"X": 5, "Y": 5}
    record_lot(db, "A", revision, a, f"intake-A-{revision}")
    record_lot(db, "B", revision, b, f"intake-B-{revision}")
    record_transfer(db, "2026-10-20", revision, "mix",
                    {("lot", "A", revision): a, ("lot", "B", revision): b}, buckets)
    return seal(db, "2026-10-20", revision)


class Mailbox:
    def __init__(self):
        self.messages = []

    def hold(self, message):
        self.messages.append(deepcopy(message))
        return len(self.messages) - 1

    def deliver(self, index, receiver):
        return receiver(deepcopy(self.messages[index]))


@contextmanager
def reject_commits(db):
    def authorize(action, arg, *_):
        if action == sqlite3.SQLITE_TRANSACTION and arg == "COMMIT":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    db.set_authorizer(authorize)
    try:
        yield
    finally:
        db.set_authorizer(None)


def legacy_probe(source_path, first_path, second_path):
    inbox = Mailbox()
    with open_source(source_path) as source:
        original = seed(source)
        message = inbox.hold(original.data)
    with open_station(first_path) as first, open_station(second_path) as second:
        first_copy = inbox.deliver(message, lambda data: accept(first, data))
        second_copy = inbox.deliver(message, lambda data: accept(second, data))
        assert inbox.deliver(message, lambda data: accept(first, data)) == first_copy
        seal_id = original.receipt.seal_id
        assert first_copy.seal_id == second_copy.seal_id == seal_id
        assert first_copy.data == second_copy.data == original.data
        pending = post(first, "old-X", seal_id, "A", ["X"])
        assert pending.status == "pending" and pending.decision.bounds.group == (1, 5)
        with reject_commits(first):
            try:
                post(first, "old-P", seal_id, "A", ["X", "Y"])
            except sqlite3.DatabaseError:
                pass
            else:
                raise AssertionError("expected a failed commit")
        try:
            retrieve(first, "old-P")
        except KeyError:
            pass
        else:
            raise AssertionError("failed commit left a posting")
        first_record = post(first, "old-P", seal_id, "A", ["X", "Y"]).posting
        second_record = post(second, "old-Q", seal_id, "A", ["Y", "X"]).posting
        p = first_record.receipt.deducted_kg
        q = second_record.receipt.deducted_kg
        assert p == q == 6
    if first_path != ":memory:":
        with open_station(first_path) as first:
            assert retrieve(first, "old-P") == first_record
        with open_station(second_path) as second:
            assert retrieve(second, "old-Q") == second_record
    return {"seal_id": seal_id, "X_kg": list(pending.decision.bounds.group),
            "V1_independent_deductions_kg": [p, q]}


def voucher_workflow(root):
    """Offline voucher life cycle with out-of-order returns and reopen replay."""
    source_path = root / "source.db"
    q_path = root / "Q.db"
    p_path = root / "P.db"
    t_path = root / "T.db"
    mail = Mailbox()

    # --- Source: freeze A=6, B=4 into X=Y=5 and issue the first two vouchers.
    with open_source(source_path) as source:
        issued = seed(source)
        seal_id = issued.receipt.seal_id
        fingerprint = issued.receipt.fingerprint
        sealed_message = mail.hold(issued.data)
        p_voucher = issue_voucher(source, "v-P", seal_id, "A", ["X"], "P", 1)
        q_voucher = issue_voucher(source, "v-Q", seal_id, "A", ["X", "Y"], "Q", 5)
        # A third kilogram on bucket X cannot be promised jointly.
        try:
            issue_voucher(source, "v-X", seal_id, "A", ["X"], "X", 1)
        except ValueError:
            overissue_rejected = True
        else:
            overissue_rejected = False
        assert overissue_rejected
        p_message = mail.hold(p_voucher)
        q_message = mail.hold(q_voucher)

    # --- Stations accept the sealed batch offline and bind their identity.
    with open_station(p_path, "P") as p_station, open_station(q_path, "Q") as q_station:
        mail.deliver(sealed_message, lambda data: accept(p_station, data))
        mail.deliver(sealed_message, lambda data: accept(q_station, data))
        p_data = mail.deliver(p_message, lambda data: data)
        q_data = mail.deliver(q_message, lambda data: data)

        # Q debits 2 kg, then hands back two separate 1 kg returns.
        first_debit = debit_voucher(q_station, q_data, "Q-debit-1", 2)
        return_r1 = return_voucher(q_station, q_data, "Q-return-R1", 1)
        return_r2 = return_voucher(q_station, q_data, "Q-return-R2", 1)
        r1_message = mail.hold(return_r1)
        r2_message = mail.hold(return_r2)
        assert first_debit["fingerprint"] == fingerprint
        # The two returns leave 2 kg available; another 3 kg action is refused.
        try:
            debit_voucher(q_station, q_data, "Q-debit-bad", 3)
        except ValueError:
            pass
        else:
            raise AssertionError("over-limit debit was accepted")

    # --- Source is disconnected from the stations and confirms out of order.
    with open_source(source_path) as source:
        blocked = []
        try:
            issue_voucher(source, "v-T", seal_id, "A", ["X", "Y"], "T", 2)
        except ValueError:
            blocked.append("before-any-confirmation")
        # R2 is confirmed before R1.
        confirm_r2 = mail.deliver(r2_message, lambda data: confirm_return(source, data))
        try:
            issue_voucher(source, "v-T", seal_id, "A", ["X", "Y"], "T", 2)
        except ValueError:
            blocked.append("after-R2-only")
        # Re-delivering R2 only replays the first confirmation.
        replay_r2 = mail.deliver(r2_message, lambda data: confirm_return(source, data))
        assert replay_r2 == confirm_r2
        # Q reopens while R1 is still in transit and debits its last 1 kg.
        with open_station(q_path, "Q") as q_station:
            second_debit = debit_voucher(q_station, q_data, "Q-debit-2", 1)
        confirm_r1 = mail.deliver(r1_message, lambda data: confirm_return(source, data))
        # Both returns are now known: 2 kg can be issued to a third station,
        # but one more kilogram cannot be promised jointly.
        t_voucher = issue_voucher(source, "v-T", seal_id, "A", ["X", "Y"], "T", 2)
        try:
            issue_voucher(source, "v-U", seal_id, "A", ["X", "Y"], "U", 1)
        except ValueError:
            blocked.append("after-both-returns")
        else:
            raise AssertionError("extra 1 kg was accepted")
        t_message = mail.hold(t_voucher)

    # --- Reopen the original station and retrieve every fixed first result.
    with open_station(q_path, "Q") as q_station:
        reopened_d1 = retrieve_voucher_action(q_station, "Q-debit-1")
        reopened_d2 = retrieve_voucher_action(q_station, "Q-debit-2")
        reopened_r1 = retrieve_voucher_action(q_station, "Q-return-R1")
        reopened_r2 = retrieve_voucher_action(q_station, "Q-return-R2")
        assert reopened_d1 == first_debit and reopened_d2 == second_debit
        assert reopened_r1 == return_r1 and reopened_r2 == return_r2
        # Same number and parameters replay the original receipt.
        assert debit_voucher(q_station, q_data, "Q-debit-1", 2) == first_debit
        q_total = q_station.execute(
            "SELECT COALESCE(SUM(kilograms), 0) FROM voucher_actions "
            "WHERE kind = 'debit'"
        ).fetchone()[0]

    # --- The third station works against its own independent database.
    with open_station(t_path, "T") as t_station:
        mail.deliver(sealed_message, lambda data: accept(t_station, data))
        t_data = mail.deliver(t_message, lambda data: data)
        t_debit = debit_voucher(t_station, t_data, "T-debit-1", 2)
        t_total = t_station.execute(
            "SELECT COALESCE(SUM(kilograms), 0) FROM voucher_actions "
            "WHERE kind = 'debit'"
        ).fetchone()[0]
    with open_station(p_path, "P") as p_station:
        p_debit = debit_voucher(p_station, p_data, "P-debit-1", 1)
        p_total = p_station.execute(
            "SELECT COALESCE(SUM(kilograms), 0) FROM voucher_actions "
            "WHERE kind = 'debit'"
        ).fetchone()[0]

    return {
        "seal_id": seal_id,
        "fingerprint": fingerprint,
        "issue_rejections": blocked,
        "overissue_rejected_at_start": overissue_rejected,
        "confirmations": [
            {"order": 1, **confirm_r2},
            {"order": 2, **confirm_r1},
        ],
        "replay_R2_result": replay_r2,
        "station_debits_kg": {
            "P": int(p_total), "Q": int(q_total), "T": int(t_total),
        },
        "combined_debits_kg": int(p_total) + int(q_total) + int(t_total),
        "reopened_Q_first_receipts": {
            "Q-debit-1": reopened_d1["kilograms"],
            "Q-debit-2": reopened_d2["kilograms"],
            "Q-return-R1": json.loads(reopened_r1)["kilograms"],
            "Q-return-R2": json.loads(reopened_r2)["kilograms"],
        },
        "P_receipt": p_debit,
        "T_receipt": t_debit,
    }


def main():
    parser = ArgumentParser(description="Inspect the V1 and voucher workflows")
    parser.add_argument("--memory", action="store_true")
    parser.add_argument("--voucher", action="store_true",
                        help="run the offline voucher issue, debit, return and replay demo")
    args = parser.parse_args()
    if args.voucher:
        if args.memory:
            raise SystemExit("the voucher demo uses on-disk databases to show reopening")
        with TemporaryDirectory() as directory:
            report = voucher_workflow(Path(directory))
        print("POINTWISE_VOUCHER_SCENARIO " + json.dumps(report, sort_keys=True))
        return
    if args.memory:
        report = legacy_probe(":memory:", ":memory:", ":memory:")
    else:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            report = legacy_probe(root / "source.db", root / "P.db", root / "Q.db")
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
