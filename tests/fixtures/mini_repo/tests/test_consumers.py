from src.admin.tools import impersonate
from src.api.login import login, login_default, login_short
from src.api.refresh import refresh, refresh_twice
from src.legacy.compat import legacy_login
from src.registry import dispatch
from src.workers.cleanup import rotate


def test_login():
    assert login("alice").ttl == 3600


def test_login_short():
    assert login_short("alice").ttl == 60


def test_login_default():
    assert login_default("alice").ttl == 3600


def test_refresh():
    assert refresh("bob", 90).ttl == 90


def test_refresh_twice():
    assert refresh_twice("bob").ttl == 240


def test_rotate():
    assert rotate("carol").ttl == 0


def test_impersonate():
    assert impersonate("admin:root", "carol").ttl == 30


def test_legacy_login():
    assert legacy_login("dave", 45).ttl == 45


def test_dispatch_reaches_create():
    """The dynamic path. Nothing static connects this call to `create`."""
    assert dispatch("create", "erin", 15).ttl == 15
