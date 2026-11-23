import json
from dataclasses import asdict, dataclass

from assessment.bounds import BoundsResult
from assessment.eligibility import AssessmentDecision
from assessment.requests import request_identity
from intake.source_book import write_transaction

from .receipts import PostingReceipt


def initialize_settlement(connection):
    if connection.in_transaction:
        raise ValueError('settlement initialization requires an idle connection')
    connection.execute('PRAGMA foreign_keys = ON')
    with write_transaction(connection):
        connection.execute('''
            CREATE TABLE IF NOT EXISTS request_bindings (
                request_id TEXT PRIMARY KEY NOT NULL,
                identity TEXT NOT NULL,
                decision TEXT NOT NULL
            )
        ''')
        connection.execute('''
            CREATE TABLE IF NOT EXISTS postings (
                bill_id TEXT PRIMARY KEY NOT NULL,
                request_id TEXT UNIQUE NOT NULL REFERENCES request_bindings(request_id),
                seal_id TEXT NOT NULL REFERENCES accepted_batches(seal_id),
                original_kg TEXT NOT NULL,
                deducted_kg TEXT NOT NULL,
                balance_kg TEXT NOT NULL
            )
        ''')
        connection.execute('''
            CREATE TABLE IF NOT EXISTS posting_buckets (
                bill_id TEXT NOT NULL REFERENCES postings(bill_id),
                bucket_id TEXT NOT NULL,
                deducted_kg TEXT NOT NULL,
                PRIMARY KEY (bill_id, bucket_id)
            )
        ''')
        connection.execute('''
            CREATE TABLE IF NOT EXISTS posting_receipts (
                bill_id TEXT PRIMARY KEY NOT NULL REFERENCES postings(bill_id),
                content TEXT NOT NULL
            )
        ''')
        from .voucher_ledger import initialize_voucher_tables

        initialize_voucher_tables(connection)


@dataclass(frozen=True)
class StoredRequest:
    identity: str
    decision: AssessmentDecision
    bill_id: str | None


@dataclass(frozen=True)
class PostingRecord:
    receipt: PostingReceipt
    bucket_deductions: tuple[tuple[str, int], ...]


def _read_decision(data):
    fields = json.loads(data)
    bounds = fields['bounds']
    return AssessmentDecision(
        BoundsResult(bounds['original_kg'], tuple(bounds['group']),
                     tuple(tuple(bucket) for bucket in bounds['buckets'])),
        fields['deducted_kg'], tuple(tuple(bucket) for bucket in fields['bucket_deductions']),
    )


def _read_receipt(data):
    fields = json.loads(data)
    fields['buckets'] = tuple(fields['buckets'])
    fields['group_bounds'] = tuple(fields['group_bounds'])
    fields['bucket_bounds'] = tuple(tuple(bucket) for bucket in fields['bucket_bounds'])
    return PostingReceipt(**fields)


class SettlementLedger:
    """The settlement entry point owns the transaction for a complete decision."""

    def __init__(self, connection):
        self.connection = connection

    def find_request(self, request_id):
        row = self.connection.execute('''
            SELECT b.identity, b.decision, p.bill_id
            FROM request_bindings AS b LEFT JOIN postings AS p USING (request_id)
            WHERE b.request_id = ?
        ''', (request_id,)).fetchone()
        if row is None:
            return None
        return StoredRequest(row[0], _read_decision(row[1]), row[2])

    def _bind(self, request, decision):
        self.connection.execute('''
            INSERT INTO request_bindings (request_id, identity, decision) VALUES (?, ?, ?)
        ''', (request.request_id, request_identity(request), json.dumps(asdict(decision))))

    def commit_pending(self, request, decision):
        if not self.connection.in_transaction or decision.status != 'pending':
            raise ValueError('pending binding requires a pending decision and a transaction')
        self._bind(request, decision)

    def commit_posting(self, request, decision, receipt):
        if not self.connection.in_transaction or decision.status != 'posted':
            raise ValueError('posting requires a signable decision and a transaction')
        self._bind(request, decision)
        self.connection.execute('''
            INSERT INTO postings (bill_id, request_id, seal_id, original_kg, deducted_kg, balance_kg)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (receipt.bill_id, request.request_id, request.seal_id, str(receipt.original_kg),
              str(receipt.deducted_kg), str(receipt.balance_kg)))
        self.connection.executemany('''
            INSERT INTO posting_buckets (bill_id, bucket_id, deducted_kg) VALUES (?, ?, ?)
        ''', ((receipt.bill_id, name, str(weight)) for name, weight in decision.bucket_deductions))
        self.connection.execute('INSERT INTO posting_receipts (bill_id, content) VALUES (?, ?)',
                                (receipt.bill_id, json.dumps(asdict(receipt))))
        return PostingRecord(receipt, decision.bucket_deductions)

    def get_posting(self, identifier, *, by='request'):
        if by == 'request':
            column = 'request_id'
        elif by == 'bill':
            column = 'bill_id'
        else:
            raise ValueError('lookup must use request or bill identity')
        row = self.connection.execute(f'''
            SELECT p.bill_id, p.request_id, p.seal_id, p.original_kg, p.deducted_kg,
                   p.balance_kg, r.content
            FROM postings AS p LEFT JOIN posting_receipts AS r USING (bill_id)
            WHERE p.{column} = ?
        ''', (identifier,)).fetchone()
        if row is None:
            return None
        bill_id, request_id, seal_id, original, deducted, balance, content = row
        if content is None:
            raise ValueError(f'posting {bill_id} has no receipt')
        receipt = _read_receipt(content)
        if (receipt.bill_id, receipt.request_id, receipt.seal_id, receipt.original_kg,
                receipt.deducted_kg, receipt.balance_kg) != (
                bill_id, request_id, seal_id, int(original), int(deducted), int(balance)):
            raise ValueError(f'posting {bill_id} disagrees with its receipt')
        rows = self.connection.execute('''
            SELECT bucket_id, deducted_kg FROM posting_buckets WHERE bill_id = ? ORDER BY bucket_id
        ''', (bill_id,))
        return PostingRecord(receipt, tuple((name, int(weight)) for name, weight in rows))
