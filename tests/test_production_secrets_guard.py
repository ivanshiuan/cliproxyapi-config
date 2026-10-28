"""An internet-facing deployment must not start on the built-in dev admin credentials.

RESTO_ADMIN_PASSCODE / RESTO_SESSION_SECRET default to values that sit in a public repository, so with the
defaults anyone can log in to the admin console (or forge its cookie). ``enforce_production_secrets`` runs
first thing at API startup and refuses unless both are really set; a developer's machine is exempt.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from restaurant_api.config import (
    MIN_ADMIN_PASSCODE_LEN,
    MIN_SESSION_SECRET_LEN,
    InsecureConfigurationError,
    Settings,
    admin_credential_problems,
    enforce_production_secrets,
    get_settings,
    is_local_env,
)

GOOD_PASSCODE = "Test-Passcode-2026"
GOOD_SECRET = "test-only-" + "s" * 40  # built at runtime: no high-entropy literal in the repository
REPO = Path(__file__).resolve().parents[1]
ADMIN_VARS = ("RESTO_ENV", "RESTO_ADMIN_PASSCODE", "RESTO_SESSION_SECRET")


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    """No admin variable from the host environment, and no cached Settings, before or after."""
    for name in ADMIN_VARS:
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def make(**over) -> Settings:
    """Explicit values only: init arguments beat the environment, and no .env file is read."""
    values = {"env": "prod", "admin_passcode": GOOD_PASSCODE, "session_secret": GOOD_SECRET, **over}
    return Settings(_env_file=None, **values)


def refusal(**over) -> str:
    with pytest.raises(InsecureConfigurationError) as caught:
        enforce_production_secrets(make(**over))
    return str(caught.value)


@pytest.mark.parametrize("env", ["prod", "PROD", " prod ", "production", "staging", "stage", "prd", ""])
def test_the_dev_defaults_are_refused_outside_a_dev_machine(env):
    """Anything that is not plainly a dev machine counts as internet-facing, typos included."""
    defaults = Settings(_env_file=None, env=env)
    with pytest.raises(InsecureConfigurationError) as caught:
        enforce_production_secrets(defaults)
    message = str(caught.value)
    assert "RESTO_ADMIN_PASSCODE" in message and "RESTO_SESSION_SECRET" in message


@pytest.mark.parametrize("env", ["dev", "DEV", "development", "local", "test", "ci"])
def test_a_developer_machine_keeps_the_defaults(env):
    assert is_local_env(env)
    enforce_production_secrets(Settings(_env_file=None, env=env))  # no raise


def test_each_credential_is_checked_on_its_own():
    only_passcode = refusal(admin_passcode=Settings.model_fields["admin_passcode"].default)
    assert "RESTO_ADMIN_PASSCODE" in only_passcode and "RESTO_SESSION_SECRET" not in only_passcode
    only_secret = refusal(session_secret=Settings.model_fields["session_secret"].default)
    assert "RESTO_SESSION_SECRET" in only_secret and "RESTO_ADMIN_PASSCODE" not in only_secret


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("admin_passcode", "change-me-to-a-store-passcode"),  # copied from .env.example
        ("session_secret", "change-me-to-a-long-random-string"),  # copied from .env.example
        ("admin_passcode", "ChangeMe-Admin-2026"),  # markers match regardless of case
        ("session_secret", "x" * 40 + "-insecure"),
        ("admin_passcode", "1234567"),  # one character short
        ("session_secret", "s" * (MIN_SESSION_SECRET_LEN - 1)),
        ("admin_passcode", "        "),  # whitespace only
        ("session_secret", ""),
    ],
)
def test_example_text_and_weak_values_are_refused(field, value):
    assert admin_credential_problems(make(**{field: value})), (field, value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("admin_passcode", "x" * MIN_ADMIN_PASSCODE_LEN),  # exactly the minimum is fine
        ("session_secret", "s" * MIN_SESSION_SECRET_LEN),
    ],
)
def test_the_minimum_length_itself_is_accepted(field, value):
    assert admin_credential_problems(make(**{field: value})) == []


def test_real_credentials_start_normally():
    enforce_production_secrets(make())  # no raise
    enforce_production_secrets(make(env="staging"))


def test_the_refusal_never_echoes_a_value():
    """The message goes to the container log: it names the variables, never what they contain."""
    weak_passcode, weak_secret = "short7!", "tooshort-secret-distinct"
    for message in (
        refusal(admin_passcode=weak_passcode, session_secret=weak_secret),
        refusal(
            admin_passcode=Settings.model_fields["admin_passcode"].default,
            session_secret=Settings.model_fields["session_secret"].default,
        ),
    ):
        for value in (
            weak_passcode,
            weak_secret,
            Settings.model_fields["admin_passcode"].default,
            Settings.model_fields["session_secret"].default,
        ):
            assert value not in message


async def test_the_app_will_not_start_on_default_credentials(monkeypatch):
    monkeypatch.setenv("RESTO_ENV", "prod")
    from restaurant_api.main import app, lifespan

    with pytest.raises(InsecureConfigurationError):
        async with lifespan(app):
            pytest.fail("the app started with the built-in dev credentials")


def test_a_real_server_process_exits_with_the_reason(tmp_path):
    """The actual deployment path: uvicorn must exit non-zero and say which variables are wrong."""
    env = {k: v for k, v in os.environ.items() if k not in ADMIN_VARS}
    env["RESTO_ENV"] = "prod"
    done = subprocess.run(
        [sys.executable, "-m", "uvicorn", "restaurant_api.main:app", "--port", "8765", "--log-level", "warning"],
        cwd=tmp_path,  # no .env here, so nothing but the environment above is read
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    output = done.stdout + done.stderr
    assert done.returncode != 0
    assert "RESTO_ADMIN_PASSCODE" in output and "RESTO_SESSION_SECRET" in output


def test_the_production_compose_file_forces_production_mode():
    """Without this, forgetting RESTO_ENV in .env.production silently turns the whole check off."""
    yaml = pytest.importorskip("yaml")
    compose = yaml.safe_load((REPO / "restaurant_api" / "docker-compose.production.yml").read_text(encoding="utf-8"))
    environment = compose["services"]["api"]["environment"]
    if isinstance(environment, list):
        environment = dict(item.split("=", 1) for item in environment)
    assert environment["RESTO_ENV"] == "prod"
