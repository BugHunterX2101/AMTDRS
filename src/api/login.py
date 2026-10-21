from src.auth.session import create


def login(user):
    return create(user, 3600)


def login_short(user):
    return create(user, 60)


def login_default(user):
    return create(user)
