.PHONY: install lint format test check cli demo-validate

install:
	python3 -m pip install -e ".[dev]"

lint:
	python3 -m ruff check .
	python3 -m mypy apps packages

format:
	python3 -m ruff format .
	python3 -m ruff check --fix .

test:
	python3 -m pytest

check: lint test

cli:
	python3 -m agentskill_eval_cli --help

demo-validate:
	python3 -m agentskill_eval_cli dataset validate examples/datasets/python-review-demo
