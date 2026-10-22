"""Backport the 921600 divisor from Linux v5.10 ch341_get_divisor.

Source: https://github.com/torvalds/linux/blob/v5.10/drivers/usb/serial/ch341.c
Limit this correction to the board's verified bridge baudrate. Other rates keep
the vendor 5.4 driver behavior. The source transformation is checked/idempotent.
"""
from pathlib import Path
import sys

OLD = "\ta = (factor & 0xff00) | divisor;"
NEW = OLD + """

\t/* Linux v5.10 divisor formula: 48 MHz / (4 * 13) = 923076.9.
\t * The old factor truncation produces 857142.9 for requested 921600.
\t * ps=3, fact=1, div=13; retain the latency flag below.
\t */
\tif (priv->baud_rate == 921600)
\t\ta = 0xf307;"""


def patch(path):
    source = path.read_text()
    if NEW in source:
        return
    if source.count(OLD) != 1:
        raise RuntimeError("Unexpected CH341 source; refusing to patch")
    path.write_text(source.replace(OLD, NEW))


if __name__ == "__main__":
    patch(Path(sys.argv[1]))
