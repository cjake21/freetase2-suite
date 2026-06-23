# Sphinx configuration for the TASE.2 testbed node documentation.
# Read the Docs style: sphinx_rtd_theme + MyST (Markdown) pages.
import os

project = "FreeTASE2 Suite"
author = "FreeTASE2 Suite contributors"
copyright = "FreeTASE2 Suite contributors"

# Version from the repository VERSION file.
_here = os.path.dirname(os.path.abspath(__file__))
try:
    with open(os.path.join(_here, "..", "VERSION")) as _f:
        release = _f.read().strip()
except OSError:
    release = "0.0.0"
version = release

extensions = [
    "myst_parser",
]

# Custom templates (overrides the page <title> separator; no em dash).
templates_path = ["_templates"]

# Do not auto-convert -- or --- into en/em dashes in rendered text.
smartquotes = False

myst_enable_extensions = [
    "colon_fence",   # ::: admonitions
    "deflist",
    "tasklist",
]
myst_heading_anchors = 3

source_suffix = {
    ".md": "markdown",
    ".rst": "restructuredtext",
}

# Legacy capture/proof files live alongside these docs but are not part of the
# structured portal; keep them out of the build so there are no stray pages.
exclude_patterns = [
    "_build", "Thumbs.db", ".DS_Store",
    "capture_decode.txt", "proof_client.txt", "proof_probe.txt",
    "lab_notes.md", "lab_device_runbook.md", "README.md",
]

html_theme = "sphinx_rtd_theme"
html_theme_options = {
    "collapse_navigation": False,   # keep nested sections expandable
    "sticky_navigation": True,
    "navigation_depth": 3,
    "titles_only": False,
    "style_external_links": True,
}
html_title = "FreeTASE2 Suite documentation"
html_show_sourcelink = False

# A short note shown under the logo area.
html_context = {
    "display_github": False,
}
