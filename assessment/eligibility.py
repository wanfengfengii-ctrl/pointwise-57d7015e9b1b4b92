from dataclasses import dataclass

from .bounds import BoundsResult


@dataclass(frozen=True)
class AssessmentDecision:
    bounds: BoundsResult
    deducted_kg: int | None
    bucket_deductions: tuple[tuple[str, int], ...]

    @property
    def status(self):
        return 'pending' if self.deducted_kg is None else 'posted'

    @property
    def balance_kg(self):
        if self.deducted_kg is None:
            return None
        return self.bounds.original_kg - self.deducted_kg


def decide_eligibility(bounds):
    low, high = bounds.group
    if low != high:
        return AssessmentDecision(bounds, None, ())
    bucket_deductions = tuple((name, minimum) for name, minimum, maximum in bounds.buckets
                              if minimum == maximum)
    return AssessmentDecision(bounds, low, bucket_deductions)
