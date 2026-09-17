"""Keep pip compatibility manifests aligned with the canonical project metadata."""
import argparse
import tomllib
from pathlib import Path

root = Path(__file__).resolve().parent.parent
project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--check", action="store_true")
args = parser.parse_args()
for name, dependencies in {
    "requirements.txt": project["dependencies"],
    "requirements-dev.txt": project["optional-dependencies"]["dev"],
}.items():
    content = "# Generated from pyproject.toml by scripts/sync-requirements.py.\n" + "\n".join(dependencies) + "\n"
    target = root / name
    if args.check:
        if target.read_text() != content:
            raise SystemExit(f"{name} is stale; run python scripts/sync-requirements.py")
    else:
        target.write_text(content)
