You are writing **characterisation tests** for one Python symbol.

A characterisation test asserts what the code **currently does**, including the
parts that look wrong, with no judgement about whether that behaviour is
correct. It is not a specification test. If the function returns `None` on an
empty input, you assert that it returns `None` — you do not assert that it
raises, however much it ought to.

You have not been told why these tests are wanted, and you must not speculate.
Write down the behaviour that is in front of you.

## The symbol

File: `{target_path}`
Symbol: `{target_fqn}`
Signature: `{signature}`

```python
{source}
```

## How it is reached

{call_sites_block}

## How this repository writes tests

{convention_block}

## Rules

1. **At most {max_tests} tests.** Fewer tests that each pin a distinct behaviour
   beat many that restate the same one. You are scored on whether your tests
   detect changes to this symbol, not on how many you wrote.
2. **Assert observable behaviour**: return values, raised exception types, and
   calls made to collaborators you were given. Never assert on private
   attributes or on the source text.
3. **Every test must pass against the code exactly as shown.** A test that fails
   here is discarded, and a discarded test protects nothing. Trace the code by
   hand before asserting a value.
4. **Pin the edge cases**, because that is where a refactor breaks: empty input,
   zero, `None`, a boundary comparison, the branch that returns early.
5. **No network, no filesystem, no clock, no randomness.** Use the standard
   library and `pytest` only. If the symbol requires a collaborator, construct a
   minimal fake inline.
6. **Import the symbol the way the call sites above import it.**
7. Give each test a name that says which behaviour it pins, for example
   `test_returns_none_when_the_session_has_expired`.

Return **only** a single Python module, in one fenced block, with no prose before
or after it:

```python
# your test module
```
