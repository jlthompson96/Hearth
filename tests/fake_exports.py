"""Fake Fidelity positions exports.

The header is real — it is the one Phase 2 was blocked on, copied from an
actual download. Every row under it is invented: the accounts, the symbols, the
figures. Nothing here came from a real export and nothing should.

What the rows below the header look like was never seen, only reasoned about,
so the cash line, the pending line and the disclaimer footer are guesses at
the shape. The importer is strict precisely so that a wrong guess fails loudly
on the first real file rather than importing something plausible.
"""

HEADER = (
    "Account name,Symbol,Description,Quantity,Last price,Last price change,"
    "Current value,Today's gain/loss dollar,Today's gain/loss percent,"
    "Total gain/loss dollar,Total gain/loss percent,Percent of account,"
    "Cost basis total,Average cost basis,Type"
)

FILENAME = "Portfolio_Positions_Sep-21-2026.csv"

#: Joint Brokerage: 20,100.00 + 5,025.00 - 50.00 = 25,075.00
#: Roth IRA:        21,600.00
ROWS = (
    'Joint Brokerage,FAKEX,FAKE TOTAL MARKET FUND,100.5,$200.00,+$1.25,"$20,100.00",'
    '+$125.63,+0.63%,"+$2,100.00",+11.67%,80.00%,"$18,000.00",$179.10,Cash',
    'Joint Brokerage,CASHX**,HELD IN MONEY MARKET,,,,"$5,025.00",,,,,20.00%,,,Cash',
    "Joint Brokerage,Pending activity,,,,,-$50.00,,,,,,,,",
    'Roth IRA,FAKEB,FAKE BOND FUND,300,$72.00,-$0.10,"$21,600.00",-$30.00,-0.14%,'
    '-$400.00,-1.82%,100.00%,"$22,000.00",$73.33,Cash',
)

FOOTER = (
    "",
    '"The data and information in this spreadsheet is provided to you solely for your use."',
    '"Date downloaded Sep-21-2026 4:05 p.m ET"',
)

BALANCES = {"Joint Brokerage": "25075.00", "Roth IRA": "21600.00"}


def export(
    *rows: str,
    header: str = HEADER,
    footer: tuple[str, ...] = FOOTER,
    bom: bool = True,
    newline: str = "\r\n",
) -> bytes:
    """The bytes of a fake download. Defaults to the full fixture, with the
    byte-order mark and CRLF line endings a Windows-produced export tends to
    carry, so the plain case is also the awkward one."""
    lines = [header, *(rows or ROWS), *footer]
    text = newline.join(lines) + newline
    return ("\ufeff" if bom else "").encode() + text.encode("utf-8")
