You are planning a **relocation** refactor: code is moving to a more appropriate
place in the codebase, and every import path that names its old location has to
follow it.

This is not interface evolution. The symbol's signature does not change, its
behaviour does not change, and its callers do not change how they call it. What
changes is where it lives and therefore how it is imported.

## Goal

{goal}

## What is moving

`{target_fqn}` — `{signature}`
Currently at `{target_path}:{target_line}`

## Files that import it

{files_block}

## Import sites

{call_sites_block}

## Could not be resolved statically

{unresolved_block}

These are the dangerous ones for a relocation specifically. A module imported by
a name computed at runtime breaks *at runtime*, not at import time, and no test
that does not exercise that path will catch it. List every one you are not
handling in `unhandled` with the reason.

## Rules

1. One task per file. A task may move the target's definition, or update import
   statements in a consumer, never both in one task.
2. The task that moves the definition declares the moved symbol names in
   `declared_removals`. A move reads as a removal plus an addition to the diff
   parser, and without the declaration the behaviour gate fails the job for
   deleting an exported symbol.
3. Do not change the symbol's signature, its body, or its behaviour. If the goal
   appears to ask for a behaviour change as well as a move, plan only the move
   and record the rest in `unhandled`.
4. Do not rename the symbol while moving it. A move and a rename are two
   refactors; doing both at once makes the diff unreviewable and makes a partial
   failure impossible to interpret.
5. Every `target_file` must be one of the files listed above.
6. Update the docstring of any symbol whose import path you change only if the
   docstring names the old path. No other documentation edits.

Return only a JSON object matching this schema:

{schema}
