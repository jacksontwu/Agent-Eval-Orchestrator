.PHONY: test-controller-lifecycle shellcheck-controller-lifecycle

BATS ?= bats

test-controller-lifecycle:
	$(BATS) tests/controller-lifecycle/

shellcheck-controller-lifecycle:
	shellcheck scripts/aeo-controller.sh
