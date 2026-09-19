from src.auth.session import create


def impersonate(admin, target):
    if not admin.startswith("admin:"):
        raise PermissionError(admin)
    return create(target, 30)
