from assessment.accepted_batches import AcceptedBatchStore
from intake.weighings import require_kilograms, require_label

from .ledger import SettlementLedger


def check_mass_equation(original_kg, deducted_kg, balance_kg):
    require_kilograms(original_kg)
    require_kilograms(deducted_kg)
    require_kilograms(balance_kg)
    if original_kg != deducted_kg + balance_kg:
        raise ValueError('original mass does not equal deduction plus balance')


def retrieve(connection, identifier, *, by='request'):
    identifier = require_label(identifier, 'posting lookup')
    record = SettlementLedger(connection).get_posting(identifier, by=by)
    if record is None:
        raise KeyError(identifier)
    receipt = record.receipt
    check_mass_equation(receipt.original_kg, receipt.deducted_kg, receipt.balance_kg)
    accepted = AcceptedBatchStore(connection).get(receipt.seal_id)
    if accepted is None or (accepted.fingerprint, accepted.package.batch, accepted.package.revision) != (
            receipt.fingerprint, receipt.batch, receipt.revision):
        raise ValueError('receipt does not identify its accepted evidence')
    proven = tuple((name, low) for name, low, high in receipt.bucket_bounds if low == high)
    if record.bucket_deductions != proven:
        raise ValueError('bucket ledger disagrees with the original receipt')
    return record
