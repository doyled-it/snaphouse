"""snaphouse update: the self-update wrapper.

The real implementation (fetch origin/main, apply site.yml, verify, fall back to
last-good) lands in Task 3. For now this is a stub so bootstrap.sh has a command
to call: it reports that it is not implemented and exits non-zero.
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    print("snaphouse update: not implemented yet", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
