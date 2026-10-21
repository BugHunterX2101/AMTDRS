from src.auth.session import Session, create, destroy


def test_create_returns_session():
    s = create("alice", ttl=120)
    assert isinstance(s, Session)
    assert s.user == "alice"
    assert s.ttl == 120


def test_create_uses_default_ttl():
    assert create("bob").ttl == 3600


def test_create_rejects_empty_user():
    try:
        create("")
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_expired():
    assert create("carol", ttl=10).expired(10)
    assert not create("carol", ttl=10).expired(9)


def test_destroy():
    assert destroy(create("dave", ttl=50)).ttl == 0
