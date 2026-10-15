"""Check a built wheel or sdist: init a demo database, start the app, fetch a few pages.

Run against the built package, not the checkout:

    uv run --isolated --no-project --with dist/*.whl tests/smoke_test.py

Standard library only, since the package's dev dependencies aren't installed there.
"""

import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

PAGES = ["/", "/guide", "/rounds", "/people", "/admin", "/static/style.css"]


def competence(data: Path, *args: str, **kwargs) -> subprocess.Popen:
    # cwd is the temp folder, so `import competence` finds the installed package, never a checkout.
    code = "import sys; from competence.cli import main; sys.exit(main())"
    return subprocess.Popen([sys.executable, "-c", code, "--data", str(data), *args], cwd=data, **kwargs)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def get(url: str) -> str:
    with urllib.request.urlopen(url, timeout=5) as r:
        assert r.status == 200, (url, r.status)
        return r.read().decode()


def main() -> None:
    # On Windows the stopped app can hold the folder a moment longer; a leftover temp folder is harmless.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        data = Path(tmp)
        where = subprocess.run(
            [sys.executable, "-c", "import competence; print(competence.__file__)"],
            cwd=data,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        assert "site-packages" in where, f"not the installed package: {where}"

        for args in (["init", "--demo"], ["init"]):
            assert competence(data, *args).wait() == 0, args
        assert (data / "demo.sqlite").exists() and (data / "matrix.sqlite").exists()

        port = free_port()
        app = competence(data, "--demo", "--no-browser", "--port", str(port))
        try:
            base = f"http://localhost:{port}"
            for _ in range(100):
                try:
                    get(base + "/")
                    break
                except OSError:
                    assert app.poll() is None, "the app exited"
                    time.sleep(0.2)
            else:
                raise AssertionError("the app did not start")
            for page in PAGES:
                get(base + page)
            assert "Running a round" in get(base + "/guide")
        finally:
            app.terminate()
            app.wait(timeout=10)
    print("smoke test passed")


if __name__ == "__main__":
    main()
