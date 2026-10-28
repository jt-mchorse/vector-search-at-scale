"""`redact_target` keeps every libpq password form off stderr (#285).

One pattern, `(\\bpassword\\s*=\\s*)('...'|[^\\s&]+)`, served both libpq
grammars. In a URI's query string `&` separates parameters; in the keyword form
`&` is an ordinary character and an unquoted value runs to unescaped whitespace.
So `password=Ab&TopS3cret` printed `password=***&TopS3cret`, an escaped space cut
the redaction short, and `sslpassword` (the client key's passphrase) was never
matched because `\\bpassword` cannot match inside it.
"""

from __future__ import annotations

import pytest

from vector_bench.types import redact_target, unreachable_error

SECRET = "TopS3cret"


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        # keyword form
        (f"host=h password={SECRET}", "host=h password=***"),
        (f"host=h user=u password=Ab&{SECRET} connect_timeout=2", "host=h user=u password=*** connect_timeout=2"),
        (f"host=h password=a\\ b{SECRET} port=5", "host=h password=*** port=5"),
        (f"host=h password = 'a b {SECRET}' port=5", "host=h password = *** port=5"),
        (f"host=h password='it\\'s {SECRET}'", "host=h password=***"),
        (f"host=h sslpassword={SECRET}", "host=h sslpassword=***"),
        (f"host=h PASSWORD={SECRET}", "host=h PASSWORD=***"),
        # URI form
        (f"postgresql://u:{SECRET}@h/db", "postgresql://u:***@h/db"),
        (f"postgresql://u@h/db?password={SECRET}&sslmode=require", "postgresql://u@h/db?password=***&sslmode=require"),
        (f"postgresql://u@h/db?sslmode=require&sslpassword={SECRET}", "postgresql://u@h/db?sslmode=require&sslpassword=***"),
        (f"postgresql://u@h/db?password={SECRET}#frag", "postgresql://u@h/db?password=***#frag"),
    ],
)  # fmt: skip
def test_every_password_form_is_redacted(target: str, expected: str) -> None:
    shown = redact_target(target)
    assert shown == expected
    assert SECRET not in shown


@pytest.mark.parametrize(
    "target",
    [
        "host=h user=bench dbname=bench",
        "host=h user=password dbname=password",  # a *value* named password
        "host=h passfile=/home/u/.pgpass sslkey=/k/client.key",  # paths, not secrets
        "postgresql://u@h/db?sslmode=require",
        "http://localhost:6333",
    ],
)
def test_a_target_without_a_password_is_unchanged(target: str) -> None:
    assert redact_target(target) == target


@pytest.mark.parametrize(
    ("target", "password"),
    [
        (f"host=h password=Ab&{SECRET}", f"Ab&{SECRET}"),
        (f"host=h password=a\\ {SECRET}", f"a {SECRET}"),  # as libpq reads it
        (f"host=h sslpassword={SECRET}", SECRET),
        (f"postgresql://u@h/db?sslpassword={SECRET}", SECRET),
    ],
)
def test_the_sdk_message_is_scrubbed_of_the_same_secrets(target: str, password: str) -> None:
    """psycopg can quote the conninfo back, or the password as it parsed it."""
    echoed = Exception(f"bad conninfo {target!r}; password {password!r}")
    message = str(unreachable_error("PgVectorBackend", target, echoed))
    assert password not in message
    assert SECRET not in message
