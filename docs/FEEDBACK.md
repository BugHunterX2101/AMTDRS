# Engineering feedback: Nebius Token Factory, Sandboxes, and NVIDIA Nemotron 3

Submitted for the Nebius x NVIDIA Global AI Hackathon 2026, Coding and Agentic Engineering track, by the author of [Principal](../README.md).

This is written the way I would write it for a colleague on the platform team: every item is something I actually hit while building, with the version I hit it on, what it cost me, and what I think the fix is. Where I am guessing, I say so. The praise is as specific as the complaints, because "it's great" is not actionable either.

**Versions:** `contree-sdk` 0.3.6 · `openai` (Python) 3.16 · Token Factory inference `https://api.tokenfactory.nebius.com/v1/` · Sandboxes `https://api.tokenfactory.nebius.com/sandboxes/` · Nemotron 3 Nano / Super / Ultra.

---

## Summary

| # | Area | Severity | One line |
|---|---|---|---|
| 1 | Sandboxes | **High** | The operation UUID is not exposed on the public API, so cancellation and crash recovery require reaching into a private method. |
| 2 | Inference | **High** | Reasoning models can return empty `content` with the text in `reasoning_content`; this reads as success and fails silently three layers away. |
| 3 | Sandboxes | **High** | Sandbox entitlement is per-project but undiscoverable — a working key tells you nothing, and the failure is a bare 403. |
| 4 | Inference | Medium | Model id capitalisation is inconsistent between tiers of the same family. |
| 5 | Docs | Medium | Sandbox documentation shows a `fork()` call and a network-isolation flag that do not exist in the SDK. |
| 6 | Sandboxes | Medium | No egress control on `run()`, which blocks a real security story for untrusted generated code. |
| 7 | Sandboxes | Low | `tag_as` / `untag` are on the image; a tag-to-UUID lookup is not. |
| 8 | Inference | Low | Rate-limit headers are excellent but require `with_raw_response` to reach. |
| — | **Praise** | — | Content-addressed images; OpenAI compatibility; Nemotron tiering; Nano's price/performance. |

---

## What is genuinely excellent

I want to lead with this, because these are not small things and the design decisions behind them are correct.

### Content-addressed images are the right primitive, and they changed my architecture

`image.run(...)` returning a *new image*, with the same command against the same parent yielding the same UUID, is the single best design decision in the Sandboxes API. It is not a convenience feature. It is what made this project's entire thesis affordable:

- My green baseline — clone, `pip install`, full test suite — is built **once per job**. Every candidate patch for every task forks from that one image. With 3 candidates × 12 tasks, that is one dependency install instead of thirty-six. Dependency installation dominates the cost of any CI-shaped workload, so this is the difference between a plausible demo and an implausible bill.
- **Failure is free because it is private.** A candidate that breaks the build breaks its own fork. No shared mutable working tree, no cleanup, no rollback, no cross-candidate interference. "Generate three, race them, discard two" becomes a reasonable engineering choice rather than a reckless one.
- Deduplication is free and invisible. I wrote a crash-recovery path that re-runs the baseline command on restart; it returns the existing UUID and costs nothing. I did not have to build a cache, because the platform *is* one.

I built a local fake of the sandbox for offline development and the thing I had to reproduce most carefully was exactly this content-addressing, because so much of my design leans on it. That is a good sign about the primitive.

### OpenAI compatibility is done properly

Pointing the official `openai` SDK at a different `base_url` and having everything work — streaming, tool calls, structured outputs, `with_raw_response` — meant my entire model layer is the stock SDK with one changed string. No custom HTTP client, no bespoke retry logic, no adapter layer. This is worth more than it sounds: it is the difference between "I evaluated your platform" and "I shipped on your platform on day one."

### The Nemotron 3 tier split maps onto real workload shapes

Nano / Super / Ultra is not marketing segmentation — it lined up cleanly with three genuinely different jobs in my system, and I routed to all three for reasons I can defend:

- **Nano** for candidate generation. This is my highest-volume call and my most error-tolerant one, because a verification gate catches mistakes downstream. A 3B-active MoE is what makes wide fan-out economically possible; the architecture does not work if every candidate costs Ultra money. Nano's price/performance is the reason this project exists in its current form.
- **Super** for repair. Narrow, well-specified, but needs real reasoning about *why* a test failed. It sits in a retry loop, so Ultra's latency would hurt.
- **Ultra** for planning — called once per job, and every downstream decision inherits its mistakes.

The hybrid Mamba-Transformer architecture also matters for my prompt shape specifically: candidate generation feeds the model a whole blast radius (every call site, with context), which is a long, low-density prompt — the shape where linear-time state-space layers actually pay off.

---

## Issues, in order of what they cost me

### 1. The operation UUID is not on the public API — but you need it (High)

**What happens.** `contree_sdk` 0.3.6 starts work through `Contree._start_operation(request) -> UUID`. That UUID is the only handle on an in-flight operation, and `_cancel_operation` needs it. But `_start_operation` is private, and the public `ContreeImage.run(...)` returns the resulting image with no operation id attached anywhere I could find.

**Why that is a problem, concretely.** My core loop races N candidate patches and takes the first green one. When one wins, I must cancel the losers — otherwise I am paying for sandbox work whose result I have already thrown away. Cancelling a Python `asyncio.Task` does not cancel the remote operation. I also need the operation id to recover after a crash: without it, a restart cannot tell an orphaned in-flight operation from one that never started.

**What I had to do.** Monkeypatch the private method to capture the UUID as it goes past, with a `contextvars.ContextVar` to route it to the right caller under concurrency:

```python
original = self._client._start_operation

async def probed(request):
    op = await original(request)
    if (sink := _OP_SINK.get()) is not None:
        sink(str(op))
    return op

self._client._start_operation = probed
```

This works, and it is unambiguously the wrong thing to be doing. It will break on any refactor of a private method, and I have no way to be notified when it does.

**Suggested fix.** Any of these, in order of preference:
1. Return a handle from `run()` that carries `operation_id` — e.g. `image.operation_id`, or a `RunResult` with both.
2. An `on_operation: Callable[[UUID], None]` callback parameter on `run()`. This is the minimal change and matches what I ended up building around it.
3. At minimum, make `start_operation` / `cancel_operation` public and documented.

Cancellation is not an exotic need for an agentic workload. Any system that speculatively fans out will want it, and that is a large fraction of what people are building on this platform.

### 2. Empty `content` on reasoning models is a silent failure (High)

**What happens.** Reasoning-tuned models served over the OpenAI-compatible endpoint can place their output in `choices[0].message.reasoning_content` and leave `content` as an **empty string**. (I believe this is the behaviour tracked as Nebius API issue #211.)

**Why this one is nastier than it looks.** An empty string is not an exception. The HTTP call returns 200. The SDK returns a valid object. Every naive integration — `resp.choices[0].message.content` — gets `""` and carries on. The failure then surfaces several layers away as "the agent produced no patch", which points the debugging effort at the *prompt* and the *parser*, neither of which is broken. I would expect this to cost a typical hackathon team several hours, and to cost some of them their submission.

**What I had to do.** I stopped trusting the field and built a boot-time **3×3 capability probe**: each configured model against each of three output protocols (JSON schema / tool calls / fenced text), pinning each model to the protocol it actually demonstrated, with the results printed as a table at startup. Fifteen seconds of startup cost to convert a silent failure into a legible one.

**Suggested fix.**
1. When `content` is empty and `reasoning_content` is not, **populate `content`** with the answer portion. This is what most callers mean and it is what the OpenAI-shaped contract implies.
2. If that is not acceptable, document it loudly on the model card, with the exact code to handle it — not in a changelog, on the page someone reads before their first call.
3. Best: expose a per-model capability descriptor on `GET /v1/models` — supported response formats, whether reasoning is separated, context window. Then nobody has to probe. I wrote a 3×3 prober because the platform could not answer a question it definitely knows the answer to.

### 3. Sandbox entitlement is invisible until it 403s (High)

**What happens.** Inference access and Sandboxes access are granted separately — Sandboxes per *project*, not per *key*. A key that does inference flawlessly may have no sandbox access at all, and there is no endpoint I found that answers "can this credential use Sandboxes?" You discover it as a bare 403 from the first real operation.

**Why it hurts.** For me that was the highest-risk unknown in the whole project and I could not retire it by reading anything — I had to write a probe spike. For a hackathon participant it is worse: they may build for two days against inference before discovering the track's core feature is not enabled for them, and the error message does not tell them it is a *project* entitlement rather than a bad key.

**What I had to do.** A dedicated probe, now shipped as `principal doctor`, which does one trivial disposable run and reports inference and sandbox reachability as two separate checks — with the 403 explicitly explained as "Sandboxes are not enabled for project X; inference working does not imply sandbox access."

**Suggested fix.**
1. A `GET /sandboxes/` capability or health endpoint that returns entitlement for the calling credential, with no side effects and no charge.
2. Make the 403 body say *which* entitlement is missing and *where in the console* to request it. A 403 that names the fix is worth a page of documentation.
3. Show sandbox entitlement per project in the console, next to the project id, rather than leaving it to be inferred.

### 4. Inconsistent model id capitalisation (Medium)

The three Nemotron 3 tiers I configured do not follow one convention:

```
nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B     # vendor prefix repeated, upper case
nvidia/nemotron-3-super-120b-a12b         # all lower case
nvidia/Nemotron-3-Ultra-550b-a55b         # mixed
```

Three different capitalisation schemes within one model family. If ids are case-sensitive this is a guaranteed source of 404s; if they are not, the inconsistency still breaks string comparison, dashboard grouping and config validation in everyone's code. I resolve every id against `GET /v1/models` at boot specifically so a typo fails in five seconds rather than mid-job — but that is a workaround for something that should be normalised upstream.

**Suggested fix.** Normalise ids to one convention, keep the old ones as aliases forever, and document case-sensitivity explicitly.

### 5. Documentation shows an SDK surface that does not exist (Medium)

Sandbox material I worked from showed a `fork()` call and a network-isolation flag on the run call. Neither exists in `contree-sdk` 0.3.6. Verified against the installed package:

```python
>>> from contree_sdk.sdk.objects.image import ContreeImage
>>> [n for n in dir(ContreeImage) if not n.startswith("_")]
['apply_files', 'client', 'download', 'elapsed', 'exit_code', 'ls', 'read',
 'result', 'run', 'session', 'state', 'stderr', 'stdin', 'stdout', 'tag_as',
 'untag']
```

No `fork`. And `run()`'s full signature has no network parameter:

```
run(self, command=None, *, shell=None, args=None, env=None, cwd=None,
    hostname=None, stdin=None, stdout=None, stderr=None, tag=None,
    files=None, timeout=None, disposable=True, truncate_output_at=None,
    preserve_env=False)
```

Forking is *conceptually* right — it is what content-addressing gives you — it is just spelled `run(..., disposable=False)` returning a new image. But I had designed against `fork()` and had to re-derive the real model by reading the package. I had listed network isolation as a satisfied security requirement on the strength of a documented flag; it is not satisfied (see #6).

**Suggested fix.** Generate the API reference from the shipped package, and version-stamp every example. Conceptual docs describing a spelling the SDK does not use is a worse failure than no docs, because it produces confident wrong designs.

### 6. No egress control on `run()` (Medium)

Directly downstream of #5. My system executes **model-generated code**. The honest security story for that is "it runs in an isolated microVM with no network egress." I can say the first half. I cannot say the second, because `run()` exposes no way to disable networking.

This matters beyond my project: the Coding and Agentic Engineering track is, by definition, full of people running untrusted generated code. Egress control is the difference between a sandbox and a container that happens to be somewhere else. It is also the control an enterprise security review will ask about first.

**Suggested fix.** A `network: bool | Literal["none","egress","full"] = "full"` parameter on `run()`, defaulting to current behaviour so nothing breaks. `network="none"` would let a whole category of agentic product make a claim it currently cannot.

### 7. Tag lookup is write-only (Low)

`ContreeImage` has `tag_as` and `untag`, so I can name an image. I did not find the inverse — resolve a tag to a UUID — which is what I actually want on restart: "is there already a baseline for `principal/repo@abc123`?" I keep my own tag→UUID table in SQLite, which works but duplicates state the platform already holds, and can drift.

**Suggested fix.** `client.image_by_tag(tag) -> Image | None`, or tag filtering on a list endpoint.

### 8. Rate-limit headers are good, but hard to reach (Low)

The rate-limit response headers are genuinely useful — I surface remaining requests and remaining tokens live in my dashboard and use them for backpressure. But reaching them through the OpenAI SDK means `with_raw_response`, which changes the return type and therefore ripples through the call site:

```python
raw = await client.chat.completions.with_raw_response.create(...)
headroom = _parse_rate_headers(raw.headers)
completion = raw.parse()
```

Not a bug — this is the SDK's shape, not yours. But two notes worth acting on: (a) an example of exactly this in the Token Factory docs would save everyone the discovery, since backpressure is something every serious integration needs; (b) please document the header names and their exact semantics (window, reset units) rather than leaving them to be reverse-engineered.

---

## Two smaller notes

**Sandbox cold start** was consistently good enough that I never had to design around it, which is the highest compliment available for a latency number. My bottleneck was dependency installation, and content-addressing let me pay that once.

**`preserve_env=False` as the default on `run()`** is the correct choice and I want to say so explicitly, because safe defaults usually only get mentioned when someone finds them inconvenient.

---

## If I could ask for exactly one thing

**Operation handles on the public API** (#1). Cancellation is not a niche requirement for agentic workloads — speculative fan-out is the *characteristic* pattern of this whole product category, and every system that fans out will eventually need to cancel. Right now that requires monkeypatching a private method, which means every such system is built on something that can break without warning.

A single `on_operation` callback parameter on `run()` would close it.

---

*Filed by the author of Principal. Happy to expand on any item, provide reproductions, or test a fix.*
