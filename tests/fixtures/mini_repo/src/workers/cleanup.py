from src.auth.session import create, destroy


def rotate(user):
    old = create(user, 10)
    return destroy(old)
