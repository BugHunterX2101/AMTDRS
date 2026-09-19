"""Dynamic dispatch through a string-keyed registry.

No static edge can prove this reaches `create`. The resolver records it as a
heuristic edge and the blast radius admits it, which is what keeps the PR's
completeness claim honest.
"""

from src.auth import session

HANDLERS = {"create": "create", "destroy": "destroy"}


def dispatch(name, *args):
    fn = getattr(session, HANDLERS[name])
    return fn(*args)
