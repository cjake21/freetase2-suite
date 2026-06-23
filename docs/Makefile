# Minimal Sphinx build. Create a venv and install docs/requirements.txt first:
#   python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
#   make html      # output in _build/html/index.html
SPHINXBUILD ?= sphinx-build
SOURCEDIR   = .
BUILDDIR    = _build

html:
	$(SPHINXBUILD) -b html "$(SOURCEDIR)" "$(BUILDDIR)/html"

linkcheck:
	$(SPHINXBUILD) -b linkcheck "$(SOURCEDIR)" "$(BUILDDIR)/linkcheck"

clean:
	rm -rf "$(BUILDDIR)"

.PHONY: html linkcheck clean
