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
from publication.history import initialize_publications
from settlement.ledger import initialize_settlement
from settlement.posting import post
from settlement.reconcile import retrieve


@contextmanager
def open_source(path):
    with closing(sqlite3.connect(path)) as db:
        initialize_lot_book(db)
        initialize_operations(db)
        initialize_publications(db)
        yield db


@contextmanager
def open_station(path):
    with closing(sqlite3.connect(path)) as db:
        initialize_accepted_batches(db)
        initialize_settlement(db)
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


def main():
    parser = ArgumentParser(description="Inspect the existing uncoordinated V1 workflow")
    parser.add_argument("--memory", action="store_true")
    args = parser.parse_args()
    if args.memory:
        report = legacy_probe(":memory:", ":memory:", ":memory:")
    else:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            report = legacy_probe(root / "source.db", root / "P.db", root / "Q.db")
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
