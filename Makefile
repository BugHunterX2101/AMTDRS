# Principal — the commands CI runs, so `make ci` locally means the same thing.
.DEFAULT_GOAL := help
PY ?= python

.PHONY: help install dev doctor test lint fmt arch ci serve dashboard bench radius demo docker clean

help:  ## show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	 | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n",$$1,$$2}'

install:  ## install the package and its dev extras
	$(PY) -m pip install -e ".[dev]"

dev: install  ## install everything, including the dashboard
	cd dashboard && npm install

doctor:  ## check credentials, models and sandbox access
	$(PY) -m principal.cli doctor

test:  ## run the test suite
	$(PY) -m pytest -q

lint:  ## ruff + the architecture contracts
	$(PY) -m ruff check principal mcp_code_graph bench tests spikes
	$(PY) -m ruff format --check principal mcp_code_graph bench
	$(MAKE) arch

fmt:  ## autoformat and autofix
	$(PY) -m ruff format principal mcp_code_graph bench tests spikes
	$(PY) -m ruff check --fix principal mcp_code_graph bench tests spikes

arch:  ## the dependency rule: gates must not import models
	# `python -m importlinter.cli lint` exits 0 without evaluating anything.
	# Use the console script, which is the only invocation that actually reports.
	lint-imports

ci: lint test  ## everything CI runs

serve:  ## run the API and dashboard on :8000
	$(PY) -m principal.cli serve --reload

dashboard:  ## build the dashboard into dashboard/dist
	cd dashboard && npm install && npm run build

bench:  ## run the three benchmark arms
	$(PY) -m bench.harness --tasks bench/tasks.json --arms A,B,C

radius:  ## blast radius of the fixture's target symbol
	$(PY) -m principal.cli radius --repo tests/fixtures/mini_repo \
	  --commit deadbeef --target src.auth.session.create

demo:  ## one full job against the bundled fixture, no credentials needed
	$(PY) -m principal.cli run --repo tests/fixtures/mini_repo --commit deadbeef \
	  --target src.auth.session.create --fake-sandbox \
	  --goal "make ttl keyword-only and update every call site"

docker:  ## build and run the hosted-demo image
	docker build -t principal .
	docker run --rm -p 8000:8000 principal

clean:  ## remove generated state
	rm -rf runs snapshots .model_cache .pytest_cache .ruff_cache principal.db
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
