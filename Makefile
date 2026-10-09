.PHONY: test check clean

test:
	python -m unittest discover -s tests -v

check:
	python -m compileall -q scripts tests

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type f -name '*.py[co]' -delete
