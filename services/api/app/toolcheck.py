"""Version probes the launcher runs with a fixed command line
(`python -m app.toolcheck pst`), so its system checks prove the libraries
actually load in the app's Python, not just that files exist."""

import sys


def pst() -> str:
    import pypff

    return pypff.get_version()


PROBES = {"pst": pst}


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] not in PROBES:
        print("usage: python -m app.toolcheck pst", file=sys.stderr)
        return 2
    try:
        print(PROBES[argv[0]]())
    except Exception as exc:  # report any load failure as a failed check
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
