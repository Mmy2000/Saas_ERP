"""Is the committed CSS what a fresh build produces? (CI's assets job.)

    python3 ops/compare_css.py <committed.css> <rebuilt.css>

Byte-for-byte comparison fails across operating systems: Tailwind's CSS engine (lightningcss)
precomputes color-mix() values, and Windows and Linux round their last digit differently
(.00571122 against .00571123). So everything except numbers must match exactly, and numbers may
differ by at most TOLERANCE. A stale CSS (a class added or removed, a value changed in
assets/css/app.css) still fails.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

NUMBER = re.compile(r"-?(?:\d+\.\d*|\.\d+|\d+)(?:e-?\d+)?")
TOLERANCE = 1e-3


def parts(css: str) -> tuple[list[str], list[float]]:
    return NUMBER.split(css), [float(n) for n in NUMBER.findall(css)]


def differences(committed: str, rebuilt: str) -> list[str]:
    text_a, numbers_a = parts(committed)
    text_b, numbers_b = parts(rebuilt)
    if text_a != text_b:
        at = next(i for i, (a, b) in enumerate(zip(text_a, text_b, strict=False)) if a != b)
        return [f"text differs near: {text_a[at][:80]!r} / {text_b[at][:80]!r}"]
    return [f"number {a} / {b} near {text_a[i][-40:]!r}"
            for i, (a, b) in enumerate(zip(numbers_a, numbers_b, strict=True))
            if abs(a - b) > TOLERANCE]


def main(committed: str, rebuilt: str) -> int:
    found = differences(Path(committed).read_text(encoding="utf-8"),
                        Path(rebuilt).read_text(encoding="utf-8"))
    for line in found[:10]:
        print(line)
    if found:
        print("The committed CSS is stale: run 'npm run build' and commit the result.")
        return 1
    print("The committed CSS matches a fresh build.")
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
