"""The pgvector user-data puts `PGDATA` strictly under the data volume's mount root (#237).

`user_data.sh.tftpl` formats the EBS data volume as ext4 and mounts its root,
and `mkfs.ext4` always creates `lost+found` there. The template bind-mounts
that root into the container. `initdb` refuses a data directory holding
`lost+found` ("Create a subdirectory under the mount point"), and the image's
entrypoint runs plain `initdb "$PGDATA"`, so a `PGDATA` equal to the bind's
container side leaves the instance with no Postgres. Unset, `PGDATA` is the
`postgres` image default `/var/lib/postgresql/data` -- which is where the
template binds the mount root.

CI runs `terraform validate` and `fmt`, neither of which reads what the
user-data script does, so the template is parsed here.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path, PurePosixPath

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = REPO_ROOT / "terraform" / "modules" / "pgvector" / "user_data.sh.tftpl"

# The `postgres` image's `ENV PGDATA` for the majors before 18 (the module
# pins `pg16`, D-004).
IMAGE_DEFAULT_PGDATA = "/var/lib/postgresql/data"

_MOUNT = re.compile(r'^mount\s+"\$\$\{DATA_DEV\}"\s+(\S+)\s*$', re.MULTILINE)


def _docker_run_argv(text: str) -> list[str]:
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("docker run "))
    joined: list[str] = []
    for line in lines[start:]:
        joined.append(line.rstrip().removesuffix("\\"))
        if not line.rstrip().endswith("\\"):
            break
    return shlex.split(" ".join(joined))


def pgdata_violation(text: str) -> str | None:
    """Return why `PGDATA` is unsafe for the data volume, or None if it is under the mount root."""
    mounts = _MOUNT.findall(text)
    assert len(mounts) == 1, f"expected one data-volume mount, found {mounts}"
    mount_root = PurePosixPath(mounts[0])

    argv = _docker_run_argv(text)
    env: dict[str, str] = {}
    binds: list[tuple[PurePosixPath, PurePosixPath]] = []
    for flag, value in zip(argv, argv[1:], strict=False):
        if flag == "-e":
            key, _, val = value.partition("=")
            env[key] = val
        elif flag == "-v":
            host, container = value.split(":")[:2]
            binds.append((PurePosixPath(host), PurePosixPath(container)))

    containers = [c for h, c in binds if h == mount_root]
    if len(containers) != 1:
        return f"expected one bind of the mount root {mount_root}, found {binds}"
    bind = containers[0]
    pgdata = PurePosixPath(env.get("PGDATA", IMAGE_DEFAULT_PGDATA))
    if pgdata == bind:
        return f"PGDATA {pgdata} is the mount root (its lost+found makes initdb refuse it)"
    if not pgdata.is_relative_to(bind):
        return (
            f"PGDATA {pgdata} is outside the data-volume bind {bind}; the cluster would not persist"
        )
    return None


def test_template_formats_the_volume_as_ext4() -> None:
    # The premise of the rule: ext4 is the filesystem that ships lost+found.
    assert "mkfs.ext4" in TEMPLATE.read_text(encoding="utf-8")


def test_template_pgdata_is_a_subdirectory_of_the_mount_root() -> None:
    assert pgdata_violation(TEMPLATE.read_text(encoding="utf-8")) is None


_SHAPE = """\
mount "$${{DATA_DEV}}" /var/lib/postgresql
docker run -d \\
  --name pgvector \\
  -e POSTGRES_DB=bench \\
{extra}  -v /var/lib/postgresql:/var/lib/postgresql/data \\
  pgvector/pgvector:${{image_tag}}
"""


@pytest.mark.parametrize(
    ("extra", "needle"),
    [
        ("", "is the mount root"),
        ("  -e PGDATA=/var/lib/postgresql/data \\\n", "is the mount root"),
        ("  -e PGDATA=/var/lib/postgresql/data/ \\\n", "is the mount root"),
        ("  -e PGDATA=/srv/pgdata \\\n", "outside the data-volume bind"),
    ],
    ids=["unset", "explicit-root", "trailing-slash-root", "outside-bind"],
)
def test_rule_rejects_unsafe_pgdata(extra: str, needle: str) -> None:
    violation = pgdata_violation(_SHAPE.format(extra=extra))
    assert violation is not None
    assert needle in violation


def test_rule_accepts_a_subdirectory() -> None:
    extra = "  -e PGDATA=/var/lib/postgresql/data/pgdata \\\n"
    assert pgdata_violation(_SHAPE.format(extra=extra)) is None
