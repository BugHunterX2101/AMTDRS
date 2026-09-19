from src.auth import session


def refresh(user, ttl):
    return session.create(user, ttl)


def refresh_twice(user):
    first = session.create(user, 120)
    return session.create(first.user, 240)
