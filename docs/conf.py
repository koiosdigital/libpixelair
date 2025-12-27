"""Sphinx configuration for libpixelair documentation."""

from __future__ import annotations

import os
import sys
from datetime import datetime

# Add the project root to the path
sys.path.insert(0, os.path.abspath(".."))

# Project information
project = "libpixelair"
author = "Aiden Vigue"
copyright = f"{datetime.now().year}, {author}"

# Get version from package
try:
    from libpixelair import __version__

    version = __version__
    release = __version__
except ImportError:
    version = "0.3.0"
    release = "0.3.0"

# General configuration
extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.intersphinx",
    "sphinx.ext.viewcode",
    "sphinx.ext.napoleon",
    "sphinx_autodoc_typehints",
    "myst_parser",
]

# Template paths
templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

# Source file suffixes
source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
}

# The master document
master_doc = "index"

# Autodoc settings
autodoc_default_options = {
    "members": True,
    "member-order": "bysource",
    "special-members": "__init__,__aenter__,__aexit__",
    "undoc-members": True,
    "show-inheritance": True,
}
autodoc_typehints = "description"
autodoc_typehints_description_target = "documented"
autodoc_class_signature = "separated"

# Autosummary settings
autosummary_generate = True
autosummary_imported_members = True

# Napoleon settings (Google-style docstrings)
napoleon_google_docstring = True
napoleon_numpy_docstring = False
napoleon_include_init_with_doc = True
napoleon_include_private_with_doc = False
napoleon_include_special_with_doc = True
napoleon_use_admonition_for_examples = True
napoleon_use_admonition_for_notes = True
napoleon_use_admonition_for_references = True
napoleon_use_ivar = False
napoleon_use_param = True
napoleon_use_rtype = True
napoleon_use_keyword = True
napoleon_attr_annotations = True

# Intersphinx mapping
intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "asyncio": ("https://docs.python.org/3/library/asyncio.html", None),
}

# Type hints settings
typehints_defaults = "comma"
always_document_param_types = True
typehints_document_rtype = True

# MyST Parser settings
myst_enable_extensions = [
    "colon_fence",
    "deflist",
    "tasklist",
]

# HTML output settings
html_theme = "sphinx_rtd_theme"
html_theme_options = {
    "navigation_depth": 4,
    "collapse_navigation": False,
    "sticky_navigation": True,
    "includehidden": True,
    "titles_only": False,
    "display_version": True,
    "prev_next_buttons_location": "both",
}

html_static_path = ["_static"]
html_css_files = []

# Create _static directory if it doesn't exist
import pathlib

pathlib.Path("_static").mkdir(exist_ok=True)

# Suppress warnings for missing references to external types
nitpicky = False

# Python domain settings
python_display_short_literal_types = True
