"""Code128 barcodes as SVG (§18), without a third-party library.

Numeric codes (the usual `prefix × 10⁶ + n` barcodes) use code set C, two digits per symbol,
which keeps them short enough for a jewellery tag; anything else uses code set B.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape

# Bar/space widths (in modules) of every Code128 symbol, values 0–106. Each sums to 11,
# the stop symbol (106) to 13.
PATTERNS = (
    "212222", "222122", "222221", "121223", "121322", "131222", "122213", "122312", "132212",
    "221213", "221312", "231212", "112232", "122132", "122231", "113222", "123122", "123221",
    "223211", "221132", "221231", "213212", "223112", "312131", "311222", "321122", "321221",
    "312212", "322112", "322211", "212123", "212321", "232121", "111323", "131123", "131321",
    "112313", "132113", "132311", "211313", "231113", "231311", "112133", "112331", "132131",
    "113123", "113321", "133121", "313121", "211331", "231131", "213113", "213311", "213131",
    "311123", "311321", "331121", "312113", "312311", "332111", "314111", "221411", "431111",
    "111224", "111422", "121124", "121421", "141122", "141221", "112214", "112412", "122114",
    "122411", "142112", "142211", "241211", "221114", "413111", "241112", "134111", "111242",
    "121142", "121241", "114212", "124112", "124211", "411212", "421112", "421211", "212141",
    "214121", "412121", "111143", "111341", "131141", "114113", "114311", "411113", "411311",
    "113141", "114131", "311141", "411131", "211412", "211214", "211232", "2331112",
)
START_B, START_C, CODE_B, STOP = 104, 105, 100, 106
QUIET_ZONE = 10  # modules of white space each side


def symbols(data: str) -> list[int]:
    """Symbol values including start, checksum and stop."""
    if not data or any(not 32 <= ord(ch) <= 126 for ch in data):
        raise ValueError("Code128 set B/C takes printable ASCII only")
    if data.isdigit() and len(data) >= 2:
        pairs, rest = data[: len(data) // 2 * 2], data[len(data) // 2 * 2:]
        values = [START_C] + [int(pairs[i:i + 2]) for i in range(0, len(pairs), 2)]
        if rest:
            values += [CODE_B, ord(rest) - 32]
    else:
        values = [START_B] + [ord(ch) - 32 for ch in data]
    checksum = (values[0] + sum(i * v for i, v in enumerate(values[1:], start=1))) % 103
    return [*values, checksum, STOP]


def modules(data: str) -> list[int]:
    """Alternating bar/space widths, starting with a bar."""
    return [int(width) for value in symbols(data) for width in PATTERNS[value]]


@dataclass(frozen=True)
class Barcode:
    data: str
    widths: tuple[int, ...]

    @property
    def total_modules(self) -> int:
        return sum(self.widths) + 2 * QUIET_ZONE

    def svg(self, *, height: str = "100%", css_class: str = "") -> str:
        """Scales to its box: the bars stretch, the proportions between them stay exact."""
        x, rects = QUIET_ZONE, []
        for index, width in enumerate(self.widths):
            if index % 2 == 0:
                rects.append(f'<rect x="{x}" y="0" width="{width}" height="1"/>')
            x += width
        klass = f' class="{css_class}"' if css_class else ""
        return (f'<svg xmlns="http://www.w3.org/2000/svg"{klass} '
                f'viewBox="0 0 {self.total_modules} 1" preserveAspectRatio="none" width="100%" '
                f'height="{height}" shape-rendering="crispEdges" role="img" '
                f'aria-label="{escape(self.data)}"><rect width="100%" height="1" fill="#fff"/>'
                f'<g fill="#000">{"".join(rects)}</g></svg>')


def code128(data: str) -> Barcode:
    return Barcode(data=data, widths=tuple(modules(data)))
