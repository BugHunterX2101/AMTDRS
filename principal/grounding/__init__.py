"""Web grounding for the agents: what a third-party library's documentation says.

The code graph sees only the repository. When a change reaches code that imports
a library, or a repair is chasing an exception raised inside one, the model is
otherwise working from whatever it memorised about that library. This package
asks Tavily instead, and only on those two deterministic triggers.

It is never in the accept path. `principal.gates` may not import it (an
import-linter contract), so web text can reach a prompt and nothing else: a
search result can waste a candidate, it cannot pass a gate.
"""
