"""Documentation build scripts for libpixelair."""

from __future__ import annotations

import http.server
import os
import shutil
import socketserver
import subprocess
import sys
from pathlib import Path


def get_docs_dir() -> Path:
    """Get the docs directory path."""
    return Path(__file__).parent.parent / "docs"


def get_build_dir() -> Path:
    """Get the docs build directory path."""
    return get_docs_dir() / "_build" / "html"


def build() -> None:
    """Build the Sphinx documentation."""
    docs_dir = get_docs_dir()
    build_dir = get_build_dir()

    # Clean previous build
    if build_dir.exists():
        shutil.rmtree(build_dir)

    # Run sphinx-build
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "sphinx",
            "-b",
            "html",
            str(docs_dir),
            str(build_dir),
        ],
        cwd=docs_dir.parent,
    )

    if result.returncode != 0:
        print("Documentation build failed!")
        sys.exit(1)

    print(f"\nDocumentation built successfully at: {build_dir}")
    print(f"Open in browser: file://{build_dir}/index.html")


def serve() -> None:
    """Build and serve the documentation locally."""
    build()

    build_dir = get_build_dir()
    os.chdir(build_dir)

    port = 8000
    handler = http.server.SimpleHTTPRequestHandler

    with socketserver.TCPServer(("", port), handler) as httpd:
        print(f"\nServing documentation at http://localhost:{port}/")
        print("Press Ctrl+C to stop.")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopping server...")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Documentation tools")
    parser.add_argument("command", choices=["build", "serve"], help="Command to run")
    args = parser.parse_args()

    if args.command == "build":
        build()
    elif args.command == "serve":
        serve()
