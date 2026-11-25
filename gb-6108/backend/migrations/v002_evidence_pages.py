from src.models import evidence  # Register the evidence tables.
from src.models.base import metadata


def upgrade(connection):
    # Version 2 adds per-page evidence storage and the migration ledger.
    # checkfirst keeps startup idempotent on databases that already have them.
    metadata.create_all(connection, checkfirst=True)
