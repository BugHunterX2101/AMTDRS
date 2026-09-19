"""A private helper with no test coverage.

This is the residual risk the design cannot fully close: the API delta check
covers exported symbols and the coverage floor catches anything a test touched,
but a private helper with no coverage can be removed without any gate objecting.
The fixture exists so that hole is measured rather than assumed away.
"""


def _normalise_ttl(ttl):
    return max(0, int(ttl))
