"""H9: the extension API version and the set of hook names (docs/extensions.md)."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import extensions  # noqa: E402
from ext_dummy import load_dummy  # noqa: E402


def test_app_carries_the_api_version_and_a_hook_set():
    from app import app
    assert app.acp_extension_api == extensions.EXTENSION_API_VERSION == 1
    assert isinstance(app.acp_extension_hooks, set)


def test_dummy_registers_on_a_version_it_knows():
    host = Flask("host")
    extensions.set_api_version(host)
    load_dummy("api_version_check").register(host)
    assert host.config.get("API_VERSION_CHECK_REGISTERED") is True


def test_dummy_refuses_an_unknown_version_and_says_why(caplog):
    host = Flask("host")
    host.acp_extension_api = 99
    with caplog.at_level(logging.WARNING):
        load_dummy("api_version_check").register(host)
    assert "API_VERSION_CHECK_REGISTERED" not in host.config
    assert "99" in caplog.text and "not registering" in caplog.text


def test_dummy_refuses_an_app_without_a_version():
    host = Flask("host")
    load_dummy("api_version_check").register(host)
    assert "API_VERSION_CHECK_REGISTERED" not in host.config


def test_add_hook_records_names_and_set_api_version_keeps_them():
    host = Flask("host")
    extensions.add_hook(host, "plan_fields")
    extensions.set_api_version(host)
    assert host.acp_extension_hooks == {"plan_fields"}
