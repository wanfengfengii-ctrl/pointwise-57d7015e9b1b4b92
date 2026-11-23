from intake.source_book import write_transaction
from publication.packages import decode_v1_package

from .accepted_batches import AcceptedBatchStore


def accept(connection, data):
    package = decode_v1_package(data)
    with write_transaction(connection):
        result = AcceptedBatchStore(connection).store_once(package)
    return result
