"""Prepare a complete local cache for an explicitly trusted custom model revision."""
from pathlib import Path
import shutil


def prepare_local_code(weights, modules_cache):
    weights = Path(weights).resolve()
    package = Path(modules_cache) / "transformers_modules" / weights.name
    package.mkdir(parents=True, exist_ok=True)
    (package.parent / "__init__.py").touch(exist_ok=True)
    (package / "__init__.py").touch(exist_ok=True)
    # Transformers 4.51 can omit transitive relative imports when loading custom
    # code from a local directory. Keep the complete downloaded Python package.
    for source in weights.glob("*.py"):
        target = package / source.name
        if not target.exists() or target.read_bytes() != source.read_bytes():
            shutil.copy2(source, target)
    return package
