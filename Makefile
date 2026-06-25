# FreeTASE2 Suite: friendly front door. `make` lists the targets.

.DEFAULT_GOAL := help

.PHONY: help build run console test docs docker clean

help:  ## show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN{FS=":.*?## "}{printf "  \033[1m%-10s\033[0m %s\n", $$1, $$2}'

build:  ## build libIEC61850 (pinned) and the native tools (once)
	./scripts/10_build.sh

run:  ## launch the whole tool: control console + browser
	./tase2-suite

console:  ## start the control console without opening a browser
	./tase2-suite --no-browser

test:  ## run the unit, interop, fuzz, and scenario test suites
	python3 -m unittest discover -s tests

docs:  ## build the HTML documentation (served by the console at /docs)
	./scripts/65_build_docs.sh

docker:  ## build the container image
	docker build -t freetase2-suite .

clean:  ## remove the native binaries (rebuild with `make build`)
	$(MAKE) -C src clean
