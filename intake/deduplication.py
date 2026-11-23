from .revisions import LotRevision


def resolve_revision_replay(existing: LotRevision, proposed: LotRevision):
    if existing != proposed:
        raise ValueError(f'conflicting source revision {proposed.lot_id}/{proposed.revision}')
    return existing
