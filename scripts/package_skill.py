"""Build an installable ZIP containing the complete skill, excluding runtime caches."""
import argparse
from pathlib import Path
import os
import tempfile
from zipfile import ZipFile, ZIP_DEFLATED

SKILL_NAME = "ui-assets-skill"


def package_files(skill):
    """Only installable resources belong in a release, even from a repo root."""
    files = [skill / name for name in ("SKILL.md", "requirements.txt", "README.md")]
    for path in files:
        if not path.is_file():
            raise ValueError(f"missing required package file: {path.name}")
    for folder, extensions in (("agents", {".yaml", ".yml"}),
                               ("references", {".md"}),
                               ("scripts", {".py", ".html"})):
        files.extend(p for p in (skill / folder).rglob("*")
                     if p.is_file() and p.suffix in extensions and "__pycache__" not in p.parts)
    for path in files:
        if not path.resolve().is_relative_to(skill.resolve()):
            raise ValueError("package resource escapes skill directory")
    return sorted(files)


def package(output):
    skill = Path(__file__).resolve().parents[1]
    output = Path(output).resolve()
    if output.suffix.lower() != ".zip":
        raise ValueError("output must be a .zip archive")
    files = package_files(skill)
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(suffix=".tmp", dir=output.parent)
    os.close(fd)
    try:
        with ZipFile(temporary, "w", ZIP_DEFLATED) as archive:
            for path in files:
                archive.write(path, (Path(SKILL_NAME) / path.relative_to(skill)).as_posix())
        with ZipFile(temporary) as archive:
            if archive.testzip() is not None:
                raise ValueError("invalid archive")
        os.replace(temporary, output)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    print(package(parser.parse_args().output))
