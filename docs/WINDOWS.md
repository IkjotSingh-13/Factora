# Windows / PowerShell quickstart (Mansimran)

Everything in this repo runs on Windows. Four things behave differently from Linux/macOS and
are worth knowing before they cost you time at 3 a.m.: line endings and encoding (§2), the
optional node build for the React UI (§1b), JSON redirection, and `.env` loading.

---

## 1. Setup, once

```powershell
# from the repo folder, in PowerShell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\scripts\bootstrap.ps1        # the whole project: venv, all three requirements
                                 # files, spaCy model, .env, then the test suite
```

`scripts\bootstrap.ps1` is the project-level installer (same job as `bootstrap.sh` on
macOS/Linux): it is idempotent, prints a fix line per failure, and never writes outside the
repo. `-Ollama` additionally points `.env` at a local model and pulls it.

```powershell
.\scripts\bootstrap.ps1 -SkipTests -Ollama   # no suite, plus qwen2.5:7b for S1/S3
```

`scripts\setup_windows.ps1` is the older **S2-module-only** installer (its own requirements
file, its own offline tests). Use it if you are only working on retrieval; use `bootstrap.ps1`
to work on the app.

No-PowerShell fallback (cmd.exe, ignores execution policy entirely):

```bat
scripts\setup_windows.bat
```

That creates `.venv`, installs `requests`, `pydantic`, `pytest`, copies
`.env.example` → `.env`, runs the 27 offline tests and does an offline retrieval.

Manual equivalent:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python -m pytest -q
```

Then, every new terminal: `.\.venv\Scripts\Activate.ps1`

> If `Activate.ps1` is blocked, use `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`
> in that window first (it does not change machine-wide policy), or call
> `.\.venv\Scripts\python.exe` directly and skip activation.

---

## 1b. The React UI (optional - `/lite` needs nothing)

`/` serves the React app in `frontend-app\` once it has been built; **`/lite` is the
zero-build single file**, and it is what `/` serves when `frontend-app\dist` does not exist.
Skipping this section costs you the built bundle and nothing else.

```powershell
# Node 18+ on PATH (winget install OpenJS.NodeJS.LTS). From the repo root:
.\scripts\bootstrap.ps1 -UI          # deps, then npm install + npm run build, then the suite

# ...or by hand:
cd frontend-app
npm install
npm run build                        # tsc -b && vite build  ->  dist\
cd ..
python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

- `bootstrap.ps1 -UI` prints `skip UI build stopped: <reason>` and carries on when npm is
  missing or the build fails. The installer never fails the whole run because of node, because
  `/lite` is a complete UI.
- `make build-ui` is those same two npm commands. There is no `make` on Windows by default; every
  other Makefile target is just `python scripts\<name>.py` (see §4).
- `npm run dev` (port 5173) proxies `/api` to `127.0.0.1:8000` via `frontend-app\vite.config.ts`,
  so start the API **first** or every run in the page reports `HTTP 500`.
- Long paths bite node, not Python: `node_modules\` under `OneDrive\Desktop\...` runs past
  `MAX_PATH` and npm dies with `ENAMETOOLONG` or a confusing `ENOENT`. Clone near the drive
  root (`C:\src\Factora`), or enable long paths in admin PowerShell and reboot:

  ```powershell
  Set-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' -Name LongPathsEnabled -Value 1
  ```

- `dist\` and `node_modules\` are ignored (`frontend-app\.gitignore`); `make zip` on
  macOS/Linux is what carries a built `dist\` to a teammate, so the receiving machine needs no
  node at all.
- Line endings: `.gitattributes` checks `*.ps1`/`*.bat` out as CRLF (what Windows wants), and
  `make zip` normalizes the work-tree copies before packing, so the archives a teammate unzips
  are CRLF too - verified on this tree: all four `scripts\*.ps1`/`.bat` files are pure CRLF,
  zero lone LF. `bootstrap.ps1` is also ASCII-only, enforced by
  `tests/test_s2.py::test_windows_scripts_are_ascii_only`, because PowerShell 5.1 reads a curly
  quote as a string delimiter and the error it then prints is meaningless.
- Nothing on either page loads from the internet: the two faces are in
  `frontend-app\src\assets\fonts\`, the hero clip is `frontend-app\public\hero.mp4`, the six
  card thumbnails are `frontend-app\public\hero\`. `python -m pytest tests\test_ui_serving.py`
  fails if a `src=` or `href=` pointing at a remote host is ever added to either UI.

---

## 2. The three Windows gotchas

### (a) Don't redirect JSON with `>`

PowerShell 5.1 writes `>` redirects as **UTF-16LE**, so the "JSON" file you produce
is not readable by Python or `jq`. Use the built-in flag instead:

```powershell
python s2_retrieval.py --file claim.txt --json --out out.json     # correct
python s2_retrieval.py --file claim.txt --json > out.json         # breaks on PS 5.1
```

Need it on stdout anyway? Pipe through `Out-File` with an explicit encoding:

```powershell
python s2_retrieval.py --json "claim text" | Out-File -Encoding utf8 out.json
```

### (b) Encoding can crash on real article titles

Titles from Wikipedia/PubMed contain `’ “ —`. A cp1252 console raises
`UnicodeEncodeError` and would kill the demo mid-sentence. This repo already
protects itself two ways:

- the CLI reconfigures stdout/stderr to UTF-8 with `errors="replace"`
- the PowerShell scripts set `PYTHONUTF8=1` and `PYTHONIOENCODING=utf-8`

If you ever see mojibake in your own scripts, set those two variables first:

```powershell
$env:PYTHONUTF8 = "1"; $env:PYTHONIOENCODING = "utf-8"
```

### (c) `.env` is loaded by the code, not by the shell

There is no `export` in PowerShell and people forget `$env:VAR="..."`, so
`envload.py` reads `.env` automatically (from the repo root). Precedence:

1. a variable already set in your PowerShell session **wins**
2. otherwise the value in `.env` is used

```powershell
$env:TAVILY_API_KEY = "tvly-..."      # this session only, overrides .env
python s2_retrieval.py "claim text"   # audit shows tavily active
```

---

## 3. Daily loop (PowerShell)

```powershell
.\.venv\Scripts\Activate.ps1

# work, then
.\scripts\pre_pr_check.ps1            # rebase on main + tests + contract-drift diff
git add -A
git commit -m "feat(retrieval): <what changed>"
git push origin mansimran/retrieval_and_evidence
```

`pre_pr_check.ps1` is the PowerShell twin of `pre_pr_check.sh`; use whichever
your machine supports. Both print the `Evidence` schema and warn when your
`contracts.py` drifts from `main` — that is the failure mode that actually
breaks a four-branch team.

---

## 3b. When the environment misbehaves: the doctor

```powershell
python scripts\doctor.py
.venv\Scripts\python.exe scripts\doctor.py     # check the venv explicitly
```

It prints the interpreter path, whether you are inside a venv, which required
packages import, whether `.env` was found, whether Wikipedia/OpenAlex/PubMed are
reachable, and what the demo snapshot holds. Nine times out of ten that tells
you the problem in five seconds.

`requirements-s2.txt` is the module-scoped install list. This stage installs from
it, not from the shared `requirements.txt`, so a teammate's edit cannot remove
pydantic from under retrieval.

## 4. Useful commands

```powershell
# live retrieval, one claim
python s2_retrieval.py "Vitamin C supplementation prevents the common cold in most adults" --top-k 6

# no network at all (demo fallback)
python s2_retrieval.py --offline "vitamin C common cold"

# measured source reliability + latency
python scripts/live_smoke.py

# tests only
python -m pytest -q

# what is a source doing? (per-source status lines)
python s2_retrieval.py --verbose "The Eiffel Tower is 330 metres tall."

# one verify call, parsed by PowerShell instead of written through a redirect (§2a)
Invoke-RestMethod -Uri http://localhost:8000/api/verify -Method Post -ContentType "application/json" `
  -Body (@{ article_text = "The Eiffel Tower is 330 metres tall."; mode = "offline" } | ConvertTo-Json) |
  Select-Object mode, @{ n = "claims";   e = { $_.report.claims.Count } },
                @{ n = "warnings"; e = { $_.warnings.Count } }

# which UI this process is serving: "frontend-app build at /" or "single-file UI at /"
(Invoke-RestMethod http://localhost:8000/api/config).ui
```

---

## 5. If something fails

| Symptom | Cause | Fix |
|---|---|---|
| `Activate.ps1 cannot be loaded` | execution policy | `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` |
| `Missing argument in parameter list` / `Missing closing '}'` while running a .ps1 | non-ASCII character (em dash, curly quote) inside a .ps1 string - PowerShell 5.1 treats smart quotes as string delimiters | fixed in this repo; keep .ps1 files ASCII-only (a test enforces it), or use `scripts\setup_windows.bat` |
| `UnicodeEncodeError` | console codepage | `$env:PYTHONUTF8="1"` then rerun |
| JSON file unreadable | `>` redirect wrote UTF-16 | use `--out out.json` |
| `ModuleNotFoundError: No module named 'pydantic'` (or requests/pytest) | the packages went into a different Python than the one running the file - inactive venv, the Store alias, or a teammate's requirements.txt replacing ours | run `python scripts\doctor.py`; then `".venv\Scripts\python.exe" -m pip install -r requirements-s2.txt`. Call `.venv\Scripts\python.exe` explicitly instead of relying on activation |
| `source skipped: no key` in audit | `.env` missing the key | add it to `.env`, or `$env:KEY="..."` |
| `python` opens Microsoft Store | Windows app alias | install Python from python.org with "Add to PATH", or use `py` |
| `pytest: command not found` | venv not activated | `.\.venv\Scripts\Activate.ps1` |
| Slow first call (~2 s) | cold HTTP, no cache | second call is fast; `python scripts/live_smoke.py` warms it |
| `Cannot find module 'react'` or a type-declaration error from `npm run build` | `node_modules\` is missing, or a long-path failure silently truncated it | `cd frontend-app; npm install` near the drive root, then `npm run build`; or skip node and open `/lite` |
| `/` renders blank but `/lite` works | `frontend-app\dist` was never built, or `npm run build` failed after `tsc` | read `(Invoke-RestMethod http://localhost:8000/api/config).ui` - it names the UI the process is actually serving, then rebuild or use `/lite` |
| Every run in the page says `HTTP 500` | `npm run dev` is up but the API is not, so the proxy has nothing to talk to | start `python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000` first |
