"""The UI mount is the part a judge touches first, so it gets a test.

Two frontends ship in this repo and they are not interchangeable:

* ``frontend-app/dist``  - the React build (Taranpreet's design), served at ``/``.
* ``frontend``           - the zero-build single file, served at ``/lite``.

The rule this file protects: whichever one you get, the app must say so, and the API
endpoints must never be shadowed by the static mount. When ``frontend-app/dist`` has
not been built the app must fall back to the single file at ``/`` rather than 404 -
a judge who never ran npm still gets a working UI.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "backend")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from app import main as app_module  # noqa: E402

client = TestClient(app_module.app)

REACT_READY = (app_module.APP_UI_DIR / "index.html").is_file()


def _paths() -> set[str]:
    """Every route path currently registered on the app."""
    return {getattr(route, "path", "") for route in app_module.app.routes}


def test_api_routes_survive_the_static_mount() -> None:
    """Mounting a static tree at "/" is order-sensitive; the API must still answer."""
    paths = _paths()
    for path in ("/api/verify", "/api/samples", "/api/config", "/health"):
        assert path in paths, f"{path} was shadowed by a mount"
    assert client.get("/health").status_code == 200


def test_ui_flavour_matches_what_is_actually_on_disk() -> None:
    """`UI_FLAVOUR` is what /api/config advertises, so it cannot be aspirational."""
    if REACT_READY:
        assert app_module.UI_FLAVOUR == "frontend-app"
    elif app_module.FRONTEND_DIR.exists():
        assert app_module.UI_FLAVOUR == "frontend"
    else:
        assert app_module.UI_FLAVOUR == "none"


def test_root_serves_an_html_document() -> None:
    res = client.get("/")
    if not (REACT_READY or app_module.FRONTEND_DIR.exists()):
        pytest.skip("no UI on disk in this checkout")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/html")
    assert "<div id=\"root\"" in res.text or "VerifyAI" in res.text


def test_react_build_and_fallback_are_both_reachable_when_built() -> None:
    if not REACT_READY:
        pytest.skip("frontend-app/dist not built (run: make build-ui)")
    root = client.get("/")
    lite = client.get("/lite/")
    assert root.status_code == 200
    assert "/assets/" in root.text, "the React index must reference its own hashed bundle"
    assert lite.status_code == 200
    assert "VerifyAI" in lite.text or "verifyai" in lite.text
    assert root.text != lite.text, "/ and /lite must not be the same document"


def test_config_reports_the_ui_serving_state() -> None:
    """A reader of /api/config should learn which UI they are looking at."""
    body = client.get("/api/config").json()
    assert "ui" in body
    if REACT_READY:
        assert "frontend-app" in body["ui"]
    else:
        assert "single-file" in body["ui"]


def test_react_app_has_no_hot_linked_assets() -> None:
    """The offline-demo guarantee: nothing in the shipped UI may need the internet.

    Checked on source, not on dist, so it holds whether or not anyone has run npm.
    """
    # An attribute that *loads* a resource is the problem. A URL inside an inline
    # `xmlns`, a sample article link, or prose is not a request, so those stay legal.
    loads = re.compile(r"""(?:src|href)\s*=\s*["']https?://|@import[^;]*url\(|url\(\s*["']?https?://""")

    react_styles = sorted((ROOT / "frontend-app" / "src").rglob("*.css"))
    for label, html_file in [
        ("fallback UI", app_module.FRONTEND_DIR / "index.html"),
        ("React index", ROOT / "frontend-app" / "index.html"),
        *((f"React stylesheet {f.name}", f) for f in react_styles),
    ]:
        if not html_file.is_file():
            pytest.skip(f"{label} not on disk in this checkout")
        offenders = [line.strip()[:120] for line in html_file.read_text(encoding="utf-8").splitlines() if loads.search(line)]
        assert not offenders, f"{label} hot-links remote assets: {offenders}"


def test_fabricated_client_engine_stays_deleted() -> None:
    """`verifyEngine.ts` invented quotes, sources and scores in the browser.

    It must not come back through a future merge: the UI's only data source is
    src/lib/api.ts, which reads the running pipeline.
    """
    engine = ROOT / "frontend-app" / "src" / "lib" / "verifyEngine.ts"
    assert not engine.exists(), "the scripted mock engine must stay deleted"
    api = ROOT / "frontend-app" / "src" / "lib" / "api.ts"
    if not api.is_file():
        pytest.skip("frontend-app not vendored in this checkout")
    text = api.read_text(encoding="utf-8")
    assert "/api/verify" in text
    for banned in ("wikipedia.org/wiki/", "reuters.com/fact-check", "setTimeout("):
        assert banned not in text, f"api.ts must not fabricate ({banned})"
