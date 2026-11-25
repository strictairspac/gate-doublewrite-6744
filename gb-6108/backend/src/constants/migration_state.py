from enum import StrEnum


class MigrationState(StrEnum):
    # Backfill started; the whole package is still the only readable source.
    BACKFILLING = "backfilling"
    # All pages written and verified once, but reads still come from the package.
    BACKFILLED = "backfilled"
    # Verification passed and reads now come from per-page storage.
    SWITCHED = "switched"
    # The redundant whole-package copy on the run row has been reclaimed;
    # the migration row still holds the snapshot needed to withdraw the switch.
    RECLAIMED = "reclaimed"


# Where a rendered page was actually read from.
SOURCE_PACKAGE = "整包存储"
SOURCE_PAGE = "页存储"
