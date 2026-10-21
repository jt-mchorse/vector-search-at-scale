"""An unreachable qdrant/weaviate/pgvector server is a `BackendError`, not the SDK's traceback (#256).

`run`, `load` and `scripts/hnsw_grid.py` report a `BackendError` as one
`error:` line at exit 2 (#195, #197, #209), but no adapter wrapped its SDK's
connection errors. Measured on `main` 7378977 with nothing listening:

    QDRANT_URL=http://127.0.0.1:9 vector-bench run|load --backend qdrant ...
      -> qdrant_client.http.exceptions.ResponseHandlingException  exit 1
    WEAVIATE_HOST=127.0.0.1 vector-bench run|load --backend weaviate ...
      -> weaviate.exceptions.WeaviateConnectionError               exit 1
    PGVECTOR_DSN=postgresql://u:pw@127.0.0.1:9/x vector-bench run|load ...
      -> psycopg.OperationalError                                  exit 1
    python scripts/hnsw_grid.py --backend qdrant|pgvector ...
      -> the same traceback, and an empty --out-dir left behind

The SDKs are stubbed so CI needs no service. qdrant-client is installed in CI,
so its arms raise the real exception classes. psycopg and weaviate-client are
not (and an `importorskip` arm for either would sit dormant in CI, which
test_importorskip_modules_are_installable.py refuses), so those arms install a
stand-in module whose classes carry the real names and the real hierarchy
(`psycopg.OperationalError`/`ProgrammingError` under `psycopg.Error`).
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import numpy as np
import pytest

import vector_bench.cli as cli

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import hnsw_grid  # noqa: E402

from vector_bench.types import BackendError  # noqa: E402

PASSWORD = "s3cretpw"
PG_DSN = f"postgresql://bench:{PASSWORD}@127.0.0.1:9/bench"
QDRANT_URL = "http://127.0.0.1:9"
WEAVIATE_HOST = "127.0.0.1"
REFUSED = "[Errno 111] Connection refused"


# --- stand-in SDKs ---------------------------------------------------------


def _refused_qdrant(monkeypatch: pytest.MonkeyPatch, *, probe_ok: bool = False) -> type:
    """Patch the real `QdrantClient` with one whose calls fail like a refused socket."""
    qdrant_client = pytest.importorskip("qdrant_client")
    from qdrant_client.http.exceptions import ResponseHandlingException

    class _Client:
        def __init__(self, url: str) -> None:
            self.url = url

        def get_collections(self) -> object:
            if probe_ok:
                return types.SimpleNamespace(collections=[])
            raise ResponseHandlingException(OSError(REFUSED))

        def close(self) -> None: ...

        def __getattr__(self, name: str) -> object:
            def refused(*_a: object, **_kw: object) -> object:
                raise ResponseHandlingException(OSError(REFUSED))

            return refused

    monkeypatch.setattr(qdrant_client, "QdrantClient", _Client)
    return _Client


def _fake_psycopg(monkeypatch: pytest.MonkeyPatch, connect) -> types.ModuleType:
    mod = types.ModuleType("psycopg")

    class Error(Exception): ...

    class OperationalError(Error): ...

    class ProgrammingError(Error): ...

    mod.Error = Error  # type: ignore[attr-defined]
    mod.OperationalError = OperationalError  # type: ignore[attr-defined]
    mod.ProgrammingError = ProgrammingError  # type: ignore[attr-defined]
    mod.connect = lambda info: connect(mod, info)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "psycopg", mod)
    return mod


def _refused_psycopg(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    def connect(mod: types.ModuleType, _info: str) -> object:
        raise mod.OperationalError(  # type: ignore[attr-defined]
            'connection failed: connection to server at "127.0.0.1", port 9 failed: '
            "Connection refused"
        )

    return _fake_psycopg(monkeypatch, connect)


def _fake_weaviate(monkeypatch: pytest.MonkeyPatch, connect_to_custom) -> types.ModuleType:
    root = types.ModuleType("weaviate")
    classes = types.ModuleType("weaviate.classes")
    config = types.ModuleType("weaviate.classes.config")
    exceptions = types.ModuleType("weaviate.exceptions")
    for name in (
        "WeaviateConnectionError",
        "WeaviateGRPCUnavailableError",
        "WeaviateStartUpError",
        "WeaviateTimeoutError",
    ):
        setattr(exceptions, name, type(name, (Exception,), {}))
    root.classes = classes  # type: ignore[attr-defined]
    root.exceptions = exceptions  # type: ignore[attr-defined]
    classes.config = config  # type: ignore[attr-defined]
    root.connect_to_custom = lambda **kw: connect_to_custom(exceptions, **kw)  # type: ignore[attr-defined]
    for name, mod in (
        ("weaviate", root),
        ("weaviate.classes", classes),
        ("weaviate.classes.config", config),
        ("weaviate.exceptions", exceptions),
    ):
        monkeypatch.setitem(sys.modules, name, mod)
    return exceptions


def _refused_weaviate(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    def connect_to_custom(exc: types.ModuleType, **_kw: object) -> object:
        # The real message spans two lines.
        raise exc.WeaviateConnectionError(  # type: ignore[attr-defined]
            f"Connection to Weaviate failed. Details: Error: {REFUSED}. \n"
            "Is Weaviate running and reachable at http://127.0.0.1:8080?"
        )

    return _fake_weaviate(monkeypatch, connect_to_custom)


_REFUSE = {"qdrant": _refused_qdrant, "weaviate": _refused_weaviate, "pgvector": _refused_psycopg}


@pytest.fixture
def unreachable(monkeypatch: pytest.MonkeyPatch):
    """Point every backend's env var at a dead target and stub its SDK to refuse."""
    monkeypatch.setenv("QDRANT_URL", QDRANT_URL)
    monkeypatch.setenv("WEAVIATE_HOST", WEAVIATE_HOST)
    monkeypatch.setenv("PGVECTOR_DSN", PG_DSN)

    def install(backend: str) -> None:
        _REFUSE[backend](monkeypatch)

    return install


def _one_error_line(err: str) -> str:
    lines = err.strip().splitlines()
    assert len(lines) == 1, err
    assert lines[0].startswith("error: "), err
    assert "Traceback" not in err
    assert PASSWORD not in err
    return lines[0]


# --- the CLI and the grid --------------------------------------------------

BACKENDS = ["pgvector", "qdrant", "weaviate"]
SIZE = ["--n", "60", "--dim", "64", "--queries", "5", "--top-k", "5"]


@pytest.mark.parametrize("backend", BACKENDS)
def test_run_on_an_unreachable_server_is_exit_2_with_one_error_line(
    unreachable, backend: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    unreachable(backend)
    rc = cli.main(
        ["run", "--backend", backend, *SIZE, "--run-id", "r", "--results-dir", str(tmp_path)]
    )
    line = _one_error_line(capsys.readouterr().err)
    assert rc == 2
    assert "could not connect to" in line
    assert "Connection refused" in line


@pytest.mark.parametrize("backend", BACKENDS)
def test_load_on_an_unreachable_server_is_exit_2_with_one_error_line(
    unreachable, backend: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    unreachable(backend)
    rc = cli.main(
        [
            "load",
            "--backend",
            backend,
            *SIZE,
            "--concurrency",
            "1,2",
            "--run-id",
            "l",
            "--results-dir",
            str(tmp_path),
        ]
    )
    _one_error_line(capsys.readouterr().err)
    assert rc == 2
    assert not (tmp_path / "l").exists()


@pytest.mark.parametrize("backend", BACKENDS)
def test_the_grid_on_an_unreachable_server_is_exit_2_and_leaves_no_out_dir(
    unreachable, backend: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    unreachable(backend)
    out = tmp_path / "grid"
    rc = hnsw_grid.main(
        [
            "--n-vectors",
            "60",
            "--n-queries",
            "5",
            "--dim",
            "64",
            "--top-k",
            "5",
            "--M",
            "16",
            "--ef-construction",
            "64",
            "--ef-search",
            "40",
            "--backend",
            backend,
            "--out-dir",
            str(out),
        ]
    )
    _one_error_line(capsys.readouterr().err)
    assert rc == 2
    assert not out.exists(), "an unreachable server must not leave an --out-dir behind"


# --- the adapters ----------------------------------------------------------


def test_qdrant_raises_backend_error_at_construction_chained_to_the_sdk_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _refused_qdrant(monkeypatch)
    from qdrant_client.http.exceptions import ResponseHandlingException

    from vector_bench.backends.qdrant import QdrantBackend

    with pytest.raises(BackendError, match="QdrantBackend: could not connect to") as ei:
        QdrantBackend(url=f"http://bench:{PASSWORD}@127.0.0.1:9")
    assert isinstance(ei.value.__cause__, ResponseHandlingException)
    assert "http://bench:***@127.0.0.1:9" in str(ei.value)
    assert PASSWORD not in str(ei.value)


def test_qdrant_url_that_answers_non_2xx_is_a_setup_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # A URL that reaches some other HTTP server: the probe gets a 404.
    qdrant_client = pytest.importorskip("qdrant_client")
    import httpx
    from qdrant_client.http.exceptions import UnexpectedResponse

    class _Client:
        def __init__(self, url: str) -> None: ...

        def get_collections(self) -> object:
            raise UnexpectedResponse(404, "Not Found", b"", httpx.Headers())

        def close(self) -> None: ...

    monkeypatch.setattr(qdrant_client, "QdrantClient", _Client)
    from vector_bench.backends.qdrant import QdrantBackend

    with pytest.raises(BackendError, match="could not connect to http://127.0.0.1:9"):
        QdrantBackend(url=QDRANT_URL)


def test_qdrant_server_lost_after_construction_is_a_backend_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _refused_qdrant(monkeypatch, probe_ok=True)
    from vector_bench.backends.qdrant import QdrantBackend

    b = QdrantBackend(url=QDRANT_URL)
    with pytest.raises(BackendError, match="Connection refused"):
        b.query(np.ones(4, dtype=np.float32), 2)
    with pytest.raises(BackendError, match="Connection refused"):
        b.ingest(np.ones((300, 64), dtype=np.float32), [str(i) for i in range(300)])


def test_qdrant_server_side_refusal_after_construction_keeps_its_own_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Control: only TRANSPORT errors are wrapped after construction.

    A non-2xx answer from a reachable server is the server's verdict on the
    request, not an unreachable target, so it is not relabelled.
    """
    qdrant_client = pytest.importorskip("qdrant_client")
    import httpx
    from qdrant_client.http.exceptions import UnexpectedResponse

    class _Client:
        def __init__(self, url: str) -> None: ...

        def get_collections(self) -> object:
            return types.SimpleNamespace(collections=[])

        def query_points(self, **_kw: object) -> object:
            raise UnexpectedResponse(400, "Bad Request", b"{}", httpx.Headers())

        def close(self) -> None: ...

    monkeypatch.setattr(qdrant_client, "QdrantClient", _Client)
    from vector_bench.backends.qdrant import QdrantBackend

    b = QdrantBackend(url=QDRANT_URL)
    with pytest.raises(UnexpectedResponse):
        b.query(np.ones(4, dtype=np.float32), 2)


@pytest.mark.parametrize(
    ("dsn", "shown"),
    [
        (PG_DSN, "postgresql://bench:***@127.0.0.1:9/bench"),
        (f"host=127.0.0.1 port=9 password={PASSWORD} user=bench", "password=***"),
        (f"postgresql://127.0.0.1:9/bench?password={PASSWORD}", "?password=***"),
    ],
)
def test_pgvector_raises_backend_error_at_construction_with_the_password_redacted(
    monkeypatch: pytest.MonkeyPatch, dsn: str, shown: str
) -> None:
    psycopg = _refused_psycopg(monkeypatch)
    from vector_bench.backends.pgvector import PgVectorBackend

    with pytest.raises(BackendError, match="PgVectorBackend: could not connect to") as ei:
        PgVectorBackend(conninfo=dsn)
    assert isinstance(ei.value.__cause__, psycopg.OperationalError)  # type: ignore[attr-defined]
    assert shown in str(ei.value)
    assert PASSWORD not in str(ei.value)


def test_pgvector_malformed_dsn_is_a_backend_error_and_the_echo_is_scrubbed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # psycopg raises ProgrammingError for a DSN it cannot parse, and its message
    # can quote the DSN back.
    def connect(mod: types.ModuleType, info: str) -> object:
        raise mod.ProgrammingError(f"invalid connection option in {info!r}")  # type: ignore[attr-defined]

    _fake_psycopg(monkeypatch, connect)
    from vector_bench.backends.pgvector import PgVectorBackend

    with pytest.raises(BackendError) as ei:
        PgVectorBackend(conninfo=f"host=h password={PASSWORD} bogus")
    assert PASSWORD not in str(ei.value)


def test_pgvector_a_worker_connection_refused_mid_load_is_a_backend_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`release_connections` (#212) makes every level reconnect; the server may be gone."""

    class _Conn:
        def cursor(self) -> object:
            raise AssertionError("not reached")

        def commit(self) -> None: ...

        def close(self) -> None: ...

    calls = []

    def connect(mod: types.ModuleType, _info: str) -> object:
        calls.append(1)
        if len(calls) > 1:
            raise mod.OperationalError("server closed the connection unexpectedly")  # type: ignore[attr-defined]
        return _Conn()

    _fake_psycopg(monkeypatch, connect)
    from vector_bench.backends.pgvector import PgVectorBackend

    b = PgVectorBackend(conninfo=PG_DSN)
    b.release_connections()
    with pytest.raises(BackendError, match="server closed the connection"):
        b.prepare_thread()


def test_pgvector_reachable_server_constructs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Control: a server that accepts the connection constructs as before."""

    class _Conn:
        def close(self) -> None: ...

    _fake_psycopg(monkeypatch, lambda _mod, _info: _Conn())
    from vector_bench.backends.pgvector import PgVectorBackend

    PgVectorBackend(conninfo=PG_DSN).close()


def test_weaviate_raises_one_line_backend_error_at_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exc = _refused_weaviate(monkeypatch)
    from vector_bench.backends.weaviate import WeaviateBackend

    with pytest.raises(BackendError, match="WeaviateBackend: could not connect to") as ei:
        WeaviateBackend(host=WEAVIATE_HOST)
    assert isinstance(ei.value.__cause__, exc.WeaviateConnectionError)  # type: ignore[attr-defined]
    assert "\n" not in str(ei.value)
    assert "http://127.0.0.1:8080" in str(ei.value)


@pytest.mark.parametrize("name", ["WeaviateGRPCUnavailableError", "WeaviateStartUpError"])
def test_weaviate_grpc_or_readiness_failure_is_a_backend_error(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    def connect_to_custom(exc: types.ModuleType, **_kw: object) -> object:
        raise getattr(exc, name)("not up")

    _fake_weaviate(monkeypatch, connect_to_custom)
    from vector_bench.backends.weaviate import WeaviateBackend

    with pytest.raises(BackendError, match="not up"):
        WeaviateBackend(host=WEAVIATE_HOST)


def test_weaviate_server_lost_after_construction_is_a_backend_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def near_vector(**_kw: object) -> object:
        raise exc.WeaviateConnectionError("Connection to Weaviate failed.")  # type: ignore[attr-defined]

    coll = types.SimpleNamespace(query=types.SimpleNamespace(near_vector=near_vector))
    client = types.SimpleNamespace(
        collections=types.SimpleNamespace(get=lambda _name: coll), close=lambda: None
    )
    exc = _fake_weaviate(monkeypatch, lambda _exc, **_kw: client)
    from vector_bench.backends.weaviate import WeaviateBackend

    b = WeaviateBackend(host=WEAVIATE_HOST)
    with pytest.raises(BackendError, match="could not connect to http://127.0.0.1:8080"):
        b.query(np.ones(4, dtype=np.float32), 2)


# --- redaction -------------------------------------------------------------


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("postgresql://u:pw@h:5432/db", "postgresql://u:***@h:5432/db"),
        ("postgresql://u:p@ss@h/db", "postgresql://u:***@h/db"),
        ("postgresql://u@h/db", "postgresql://u@h/db"),
        ("postgresql://h:5432/db", "postgresql://h:5432/db"),
        ("host=h password=pw user=u", "host=h password=*** user=u"),
        ("host=h PASSWORD = 'p w' user=u", "host=h PASSWORD = *** user=u"),
        (
            "postgresql://h/db?user=u&password=pw&sslmode=require",
            "postgresql://h/db?user=u&password=***&sslmode=require",
        ),
        ("http://127.0.0.1:6333", "http://127.0.0.1:6333"),
    ],
)
def test_redact_target(target: str, expected: str) -> None:
    from vector_bench.types import redact_target

    assert redact_target(target) == expected
