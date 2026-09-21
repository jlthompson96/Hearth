"""Why an import or an entry was refused.

Every refusal means nothing was written. Every message is safe to show and to
paste: it names columns, row numbers, labels and the *shapes* of cells, and
never a figure or an identifier. `kind` is what the API sends so the UI can
tell a layout problem from a conflict without parsing prose.
"""


# Named for what happened rather than suffixed with Error, like the rest of the
# codebase: at a raise site `raise Conflict(...)` reads as the outcome it is,
# and every subclass below follows the base.
class Refused(Exception):  # noqa: N818
    kind = "refused"


class UnknownLayout(Refused):
    """No normalizer was written for this header. Never guessed at."""

    kind = "unknown_layout"


class MalformedExport(Refused):
    """The file is not a well-formed export of any layout: empty, not text, or
    rows that do not line up with the header."""

    kind = "malformed"


class AccountNumberRefused(Refused):
    """Rule 4. Raised before anything is stored, because afterwards is too late."""

    kind = "account_number"


class RowRefused(Refused):
    """A row the normalizer cannot read as what its columns promise."""

    kind = "row"


class UnknownAccounts(Refused):
    """The export names accounts Hearth has no label for. Accounts are created
    by hand, because the export does not say what kind each one is."""

    kind = "unknown_accounts"


class DateMismatch(Refused):
    kind = "date_mismatch"


class Conflict(Refused):
    """Something is already recorded where this would go."""

    kind = "conflict"


class FixtureLoaded(Refused):
    """The database holds the golden fixture. Real data does not go in beside
    invented data: a net worth summed across both is neither."""

    kind = "fixture_loaded"


class DataDirProblem(Refused):
    kind = "data_dir"


class NotFound(LookupError):  # noqa: N818
    """The thing named does not exist. Its own class, so the API can answer 404
    for it without catching every LookupError — a KeyError is a LookupError too,
    and a bug is not a missing resource."""
