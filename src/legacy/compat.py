from src.auth.session import create as make_session


def legacy_login(user, seconds):
    """Aliased import. A regex over call sites misses this one; the import edge
    resolves it, which is the whole argument for a graph over grep."""
    return make_session(user, seconds)
