def require_label(value, field):
    if not isinstance(value, str) or not value.strip() or '\0' in value:
        raise ValueError(f'{field} must be a nonempty identifier')
    try:
        value.encode('utf-8')
    except UnicodeEncodeError as exc:
        raise ValueError(f'{field} must be valid UTF-8') from exc
    return value


def require_kilograms(value, *, allow_missing=False):
    if value is None and allow_missing:
        return None
    if type(value) is not int or value < 0:
        raise ValueError('weighing must be a nonnegative integer in kg')
    return value


def parse_receipt_weighing(receipt_id, kilograms):
    return require_label(receipt_id, 'receipt'), require_kilograms(kilograms)
