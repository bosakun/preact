"""Run with the Isaac 5.1 SDK launcher; preserve its already-installed NumPy."""

import argparse
import importlib.metadata
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--install", action="store_true", help="Install; default only resolves/dry-runs"
    )
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 11):
        raise SystemExit("Use the Isaac 5.1 Python 3.11 SDK launcher, not the API interpreter")
    import numpy

    version = importlib.metadata.version("numpy")
    if numpy.__version__ != version:
        raise SystemExit("SDK NumPy import and distribution disagree; resolve environment first")
    requirements = Path(__file__).resolve().parents[1] / "workers/isaac-bridge-requirements.txt"
    subprocess.run([sys.executable, "-m", "pip", "check"], check=True)
    with tempfile.TemporaryDirectory(prefix="preact-sdk-install-") as folder:
        constraint = Path(folder) / "sdk-constraints.txt"
        constraint.write_text(f"numpy=={version}\n")
        args = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "-r",
            str(requirements),
            "-c",
            str(constraint),
        ] + ([] if args.install else ["--dry-run"])
        subprocess.run(args, check=True)
    if importlib.metadata.version("numpy") != version:
        raise SystemExit("SDK NumPy changed unexpectedly; do not start GPU work")
    subprocess.run([sys.executable, "-m", "pip", "check"], check=True)


if __name__ == "__main__":
    main()
