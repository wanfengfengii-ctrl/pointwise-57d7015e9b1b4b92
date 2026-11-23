from dataclasses import dataclass

from .weighings import parse_receipt_weighing, require_label


@dataclass(frozen=True)
class LotRevision:
    lot_id: str
    revision: str
    kilograms: int
    receipt_id: str


def make_revision(lot_id, revision, kilograms, receipt_id):
    lot_id = require_label(lot_id, 'lot')
    revision = require_label(revision, 'lot revision')
    receipt_id, kilograms = parse_receipt_weighing(receipt_id, kilograms)
    return LotRevision(lot_id, revision, kilograms, receipt_id)
