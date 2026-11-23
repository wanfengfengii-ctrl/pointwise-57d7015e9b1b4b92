from intake.source_book import write_transaction
from intake.weighings import require_label

from .evidence import build_evidence
from .history import PublicationHistory
from .packages import make_v1_package
from .snapshot import capture_snapshot


def seal(connection, batch, revision):
    batch = require_label(batch, 'batch')
    revision = require_label(revision, 'batch revision')
    with write_transaction(connection):
        history = PublicationHistory(connection)
        existing = history.get_by_revision(batch, revision)
        if existing is not None:
            return existing
        snapshot = capture_snapshot(connection, batch, revision)
        evidence = build_evidence(snapshot)
        package = make_v1_package(batch, revision, evidence)
        return history.issue_once(package)
