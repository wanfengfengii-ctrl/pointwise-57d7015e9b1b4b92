from .deduplication import resolve_revision_replay
from .revisions import make_revision
from .source_book import LotBook


def record_lot(connection, lot_id, revision, kilograms, receipt_id):
    proposed = make_revision(lot_id, revision, kilograms, receipt_id)
    book = LotBook(connection)
    with book.write_transaction():
        existing = book.get_revision(proposed.lot_id, proposed.revision)
        if existing is not None:
            return resolve_revision_replay(existing, proposed)
        book.insert_revision(proposed)
    return proposed
