"""Every check a change must pass before review, in the order CI runs them:

    python ops/check.py              # lint, translations, migrations, then the test suite
    python ops/check.py --quick      # without the test suite (seconds, no database for lint)
    python ops/check.py -- -x apps/sales   # arguments after -- go to pytest

Run it with the project's virtualenv Python (.\\venv\\Scripts\\python.exe on Windows). The
migration check and the tests need the database (DATABASE_OWNER_URL, see README.md).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable

CHECKS = [
    ("lint (ruff)", [PY, "-m", "ruff", "check", "."]),
    ("translations", [PY, "ops/i18n/messages.py", "check"]),
    ("migrations match the models",
     [PY, "manage.py", "makemigrations", "--check", "--dry-run",
      "--settings=config.settings.test"]),
]


def run(title: str, command: list[str]) -> bool:
    print(f"\n== {title}: {' '.join(Path(c).name if c == PY else c for c in command)}", flush=True)
    return subprocess.run(command, cwd=ROOT, check=False).returncode == 0


def main(argv: list[str]) -> int:
    quick = "--quick" in argv
    pytest_args = argv[argv.index("--") + 1:] if "--" in argv else []
    checks = list(CHECKS)
    if not quick:
        checks.append(("tests (pytest)", [PY, "-m", "pytest", *pytest_args]))
    failed = [title for title, command in checks if not run(title, command)]
    print("\n" + (f"FAILED: {', '.join(failed)}" if failed else "All checks passed."))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
