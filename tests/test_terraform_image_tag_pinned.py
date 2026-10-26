"""Every engine module's `image_tag` default names a version (#274).

D-004 pins the Docker tags so that `make up` months later runs the engine
version that was benchmarked. The pgvector module defaulted to `pg16`, which
is a floating tag: measured on Docker Hub on 2026-10-26, `pg16` had the same
digest as `0.8.7-pg16` (pushed 2026-10-01), while `0.8.4-pg16` through
`0.8.6-pg16` (June to August) each have a different digest. Qdrant (`v1.14.0`) and Weaviate
(`1.34.0`) were pinned. This lock reads every module that runs a container
and refuses an `image_tag` default without a dotted version number, plus any
literal `image_tag = "..."` override elsewhere under `terraform/`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TERRAFORM = REPO_ROOT / "terraform"
MODULES = TERRAFORM / "modules"

# A dotted version (`0.8.7`, `v1.14.0`, `1.34.0`) or an image digest. A bare
# Postgres major (`pg16`), `latest` or a lone major (`1`) all move.
_VERSIONED = re.compile(r"(?<![0-9A-Za-z])v?\d+\.\d+|@sha256:[0-9a-f]{64}$")

_IMAGE_TAG_VAR = re.compile(r'variable\s+"image_tag"\s*\{(?P<body>.*?)\n\}', re.DOTALL)
_DEFAULT = re.compile(r'^\s*default\s*=\s*"(?P<tag>[^"]*)"', re.MULTILINE)
_LITERAL_ASSIGN = re.compile(r'^\s*image_tag\s*=\s*"(?P<tag>[^"]*)"', re.MULTILINE)


def tag_is_versioned(tag: str) -> bool:
    return _VERSIONED.search(tag) is not None


def _container_modules() -> list[Path]:
    """Modules whose user-data template runs a container from `${image_tag}`."""
    found = []
    for tmpl in sorted(MODULES.glob("*/*.tftpl")):
        text = tmpl.read_text(encoding="utf-8")
        if "docker run" in text and "${image_tag}" in text:
            found.append(tmpl.parent)
    return found


def _image_tag_default(module: Path) -> str:
    text = (module / "variables.tf").read_text(encoding="utf-8")
    var = _IMAGE_TAG_VAR.search(text)
    assert var is not None, f'{module.name}: no `variable "image_tag"` in variables.tf'
    default = _DEFAULT.search(var.group("body"))
    assert default is not None, f"{module.name}: `image_tag` has no string default"
    return default.group("tag")


def test_every_engine_module_is_enumerated() -> None:
    # Anti-vacuity: the lock must see all three engines, not an empty glob.
    assert {m.name for m in _container_modules()} == {"pgvector", "qdrant", "weaviate"}


@pytest.mark.parametrize("module", _container_modules(), ids=lambda p: p.name)
def test_image_tag_default_names_a_version(module: Path) -> None:
    tag = _image_tag_default(module)
    assert tag_is_versioned(tag), (
        f"terraform/modules/{module.name}/variables.tf defaults image_tag to "
        f"{tag!r}, which has no version number and floats (D-004, #274). Pin the "
        "versioned tag it resolves to today."
    )


def test_no_literal_image_tag_override_floats() -> None:
    offenders = []
    for tf in sorted(TERRAFORM.rglob("*.tf")):
        if ".terraform" in tf.parts:
            continue
        for m in _LITERAL_ASSIGN.finditer(tf.read_text(encoding="utf-8")):
            if not tag_is_versioned(m.group("tag")):
                offenders.append(f"{tf.relative_to(REPO_ROOT)}: {m.group('tag')!r}")
    assert not offenders, f"floating image_tag override(s): {offenders}"


def test_pgvector_tag_keeps_a_pre_18_postgres_major() -> None:
    # test_terraform_pgvector_pgdata.py assumes the image's PGDATA is
    # /var/lib/postgresql/data, which holds for Postgres majors before 18. A
    # versioned `0.8.7-pg18` would pass the lock above and break that.
    tag = _image_tag_default(MODULES / "pgvector")
    major = re.search(r"pg(\d+)", tag)
    assert major is not None, f"pgvector image_tag {tag!r} names no Postgres major"
    assert int(major.group(1)) < 18, tag


@pytest.mark.parametrize(
    ("tag", "ok"),
    [
        ("0.8.7-pg16", True),
        ("v1.14.0", True),
        ("1.34.0", True),
        ("pg16@sha256:" + "a" * 64, True),
        ("pg16", False),
        ("latest", False),
        ("1", False),
        ("v1", False),
        ("pg16-bookworm", False),
        ("", False),
    ],
)
def test_rule_table(tag: str, ok: bool) -> None:
    assert tag_is_versioned(tag) is ok
