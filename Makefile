PYTHON ?= python3

.PHONY: test lint data-check experiment readme tune misses

test:
	$(PYTHON) -m unittest discover -s tests

lint:
	ruff check . && ruff format --check .

data-check:
	$(PYTHON) tools/fetch_data.py --verify

experiment:
	$(PYTHON) experiment.py --out results/metrics.json

readme:
	$(PYTHON) demo.py --readme docs/RETRIEVAL.md --out docs/RETRIEVAL.md

tune:
	PYTHONPATH=. $(PYTHON) tools/tune_vote.py --out results/tuning.json

misses:
	PYTHONPATH=. $(PYTHON) tools/misses.py --out docs/MISSES.md
