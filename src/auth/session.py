"""Session handling. `create` is the deprecated interface under migration."""

from dataclasses import dataclass

DEFAULT_TTL = 3600


@dataclass
class Session:
    user: str
    ttl: int

    def expired(self, age: int) -> bool:
        return age >= self.ttl


def create(user, ttl=DEFAULT_TTL):
    """Positional-ttl signature. Every consumer passes ttl positionally, which is
    the reason this migration exists and the reason it touches so many files."""
    if not user:
        raise ValueError("user is required")
    return Session(user=user, ttl=int(ttl))


def destroy(session):
    return Session(user=session.user, ttl=0)
