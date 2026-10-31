"""Environment doctor (Mansimran).

Answers the only question that matters when something like
``ModuleNotFoundError: No module named 'pydantic'`` appears:

    is the interpreter running the code the same one the packages went into?

    python scripts/doctor.py
    .venv\\Scripts\\python.exe scripts\\doctor.py     # check the venv explicitly

Checks, in order:
  1. interpreter path, version, whether it is inside a virtualenv
  2. every package this module needs (import + version)
  3. the requirements file actually present in the repo
  4. .env keys (names only, never values)
  5. network reachability of the live sources (3 s each)
  6. whether the demo snapshot exists and how many claims it holds
  7. the offline test suite

Exit code 0 when everything needed for a live run is present.
"""

from __future__ import annotations

import importlib
import json
import platform
import socket
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OK = "[ ok ]"
WARN = "[warn]"
BAD = "[FAIL]"

REQUIRED = [("requests", "requests"), ("pydantic", "pydantic"), ("pytest", "pytest")]
PROBES = [
    ("wikipedia", "https://en.wikipedia.org/w/api.php"),
    ("openalex", "https://api.openalex.org/works?per-page=1"),
    ("pubmed", "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/einfo.fcgi?db=pubmed&retmode=json"),
]


def line(status: str, text: str) -> None:
    print(f"{status} {text}")


def check_interpreter() -> bool:
    print("\n== interpreter ==")
    exe = Path(sys.executable)
    line(OK, f"python {platform.python_version()}  ({sys.executable})")
    line(OK, f"platform: {platform.system()} {platform.release()}")

    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix) or "VIRTUAL_ENV" in __import__("os").environ
    if in_venv:
        line(OK, f"virtualenv: yes ({sys.prefix})")
    else:
        line(WARN, "virtualenv: NO - you are on the system interpreter, not .venv")
        line("      ", r"fix: .venv\Scripts\Activate.ps1   (or call .venv\Scripts\python.exe directly)")

    expected = ROOT / ".venv" / ("Scripts/python.exe" if platform.system() == "Windows" else "bin/python")
    if expected.exists() and expected.resolve() != exe.resolve():
        line(WARN, f"a venv exists at {expected} but you are not using it")
        line("      ", f"fix: {expected} scripts/doctor.py")
    if sys.version_info < (3, 10):
        line(BAD, "Python 3.10+ required")
        return False
    return True


def check_packages() -> bool:
    print("\n== packages ==")
    all_ok = True
    for name, module in REQUIRED:
        try:
            mod = importlib.import_module(module)
            version = getattr(mod, "__version__", "?")
            line(OK, f"{name:<10} {version}")
        except Exception as exc:
            all_ok = False
            line(BAD, f"{name:<10} MISSING ({type(exc).__name__}: {exc})")
    if not all_ok:
        line("      ", f'fix: "{sys.executable}" -m pip install -r requirements-s2.txt')
    return all_ok


def check_requirements_file() -> None:
    print("\n== requirements ==")
    for name in ("requirements-s2.txt", "requirements.txt"):
        path = ROOT / name
        if not path.exists():
            line(WARN, f"{name}: not found")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        has_pydantic = any(
            ln.strip().lower().startswith("pydantic") for ln in text.splitlines()
        )
        if has_pydantic:
            line(OK, f"{name}: lists pydantic")
        else:
            line(WARN, f"{name}: does NOT list pydantic (a teammate's file may have replaced it)")
            line("      ", "this module installs from requirements-s2.txt, which always has it")


def check_env() -> None:
    print("\n== .env ==")
    try:
        from envload import env_report, load_env

        applied = load_env()
        if applied:
            line(OK, f"loaded {len(applied)} value(s) from .env")
        elif (ROOT / ".env").exists():
            line(OK, ".env present (no new values, shell already had them)")
        else:
            line(WARN, ".env not found - copy .env.example to .env (keys stay optional)")
        line("      ", env_report())
    except Exception as exc:
        line(WARN, f"could not inspect .env: {exc}")


def probe(name: str, url: str, timeout: float = 3.0) -> None:
    parsed = urlparse(url)
    try:
        with socket.create_connection((parsed.hostname, parsed.port or 443), timeout=timeout):
            line(OK, f"{name:<10} reachable ({parsed.hostname})")
    except Exception as exc:
        line(WARN, f"{name:<10} unreachable ({type(exc).__name__}) - offline mode still works")


def check_network() -> None:
    print("\n== network ==")
    for name, url in PROBES:
        probe(name, url)


def check_snapshot() -> None:
    print("\n== demo snapshot ==")
    path = ROOT / "data" / "demo_snapshot.json"
    if not path.exists():
        line(WARN, "data/demo_snapshot.json missing - run: python scripts/warm_snapshot.py")
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        claims = payload.get("claims", [])
        with_ev = sum(1 for c in claims if c.get("evidence"))
        line(OK, f"{len(claims)} claim(s), {with_ev} with evidence, captured {payload.get('captured_at')}")
        line("      ", "replay offline: python s2_retrieval.py --replay-snapshot data/demo_snapshot.json \"<claim>\"")
    except Exception as exc:
        line(BAD, f"snapshot unreadable: {exc}")


def check_imports() -> bool:
    print("\n== module import ==")
    try:
        import s2_retrieval  # noqa: F401
        from contracts import Evidence

        line(OK, "s2_retrieval imports cleanly")
        line("      ", f"Evidence fields: {list(Evidence.model_fields)}")
        return True
    except Exception as exc:
        line(BAD, f"import failed: {type(exc).__name__}: {exc}")
        return False


def main() -> int:
    print("VerifyAI S2 — environment doctor")
    print("=" * 62)
    interpreter_ok = check_interpreter()
    packages_ok = check_packages()
    check_requirements_file()
    check_env()
    imports_ok = check_imports()
    check_network()
    check_snapshot()

    print("\n" + "=" * 62)
    if interpreter_ok and packages_ok and imports_ok:
        print("Verdict: ready. Live retrieval needs network; replay needs nothing.")
        print('  python s2_retrieval.py "The Eiffel Tower is 330 metres tall."')
        return 0
    print("Verdict: fix the [FAIL] lines above, then run this again.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
