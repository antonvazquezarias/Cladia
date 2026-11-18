"""Check the same WSL installation that Cladia uses, before setup finishes."""

import subprocess
import sys

from . import linux


def main():
    try:
        version = linux._checked(linux.wsl("--version", timeout=30), "Checking WSL version")
        linux.registered(timeout=30)
    except linux.WslPrerequisiteError as exc:
        print(exc)
        return 2
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        print(exc)
        return 1
    print(f"WSL ready: {linux.WSL}")
    print(linux._wsl_output(version))
    return 0


if __name__ == "__main__":
    sys.exit(main())
