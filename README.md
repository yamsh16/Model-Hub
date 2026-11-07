# Model Hub

A web app where users attach a trained AI / ML / LLM model, get a report on how much it
has trained, how accurate it is, and how much more training it needs — then chat with it,
right in the browser.

No existing product does all four of these together (attach, report, estimate remaining
training, chat), so this combines a few researched, separately-solved problems into one
tool. See [Research & prior art](#research--prior-art) below for what already exists and
what this project leans on.

## Current state

The MVP loop works end to end in the browser (a Next.js + Tailwind app in [web/](web/README.md)): attach an OpenAI-compatible endpoint, upload a test-set
CSV and pick the target column, run it through the endpoint to get real accuracy / F1 /
per-class scores / a confusion matrix, and chat with the model (streamed, with history kept per
endpoint). Training logs (CSV, or an MLflow run) draw a real loss curve, and the "training still
needed" card shows an estimate with a range and its assumptions. The readiness score and the
"what to do next" list are computed from whatever is loaded (evaluation, leakage check, training
log); the score says how many signals it is built from and shows its breakdown.

```bash
cd web
npm install
npm run dev
```

Then open http://localhost:3000. The backend (below) must be running on port 8001. (The earlier
plain HTML/CSS/JS version, built from the design mockup, was retired on Day 24; it is in the git
history before that commit.)

```
web/               Next.js + Tailwind app: pages /, /connect, /report, /chat (see web/README.md)
backend/
  main.py          FastAPI app (GET /health)
  endpoints.py     register / list / get / delete API endpoints (POST /endpoints probes the URL)
  testsets.py      CSV test-set upload, target column, train/test leakage check (POST /testsets)
  metrics.py       accuracy, F1, ROC-AUC, confusion matrix from predictions (POST /metrics)
  evaluation.py    runs a test set through an attached endpoint and scores it (POST /testsets/{id}/evaluate)
  chat.py          chat through an endpoint, plain or streamed as SSE (POST /endpoints/{id}/chat)
  logs.py          training-log CSV upload and MLflow import (POST /logs, POST /logs/mlflow)
  estimate.py      "training still needed" from curve-fitting a loss series, with a range and stated assumptions (GET /logs/{id}/estimate)
  readiness.py     0-100 readiness score + prioritised "what to do next" (GET /readiness?testset_id=&log_id=)
  config.py, secretbox.py, ownership.py   secrets/keys, API-key encryption, the one "does this row belong to the user" check
  scan.py, weights.py   structural scanner for safetensors / GGUF files; upload, list, delete (POST /weights)
  security.py      protective response headers (nosniff, no-store, CSP, ...); docs are off with MODELHUB_ENV=production
  sandbox.py, serving.py, storage.py   run an uploaded GGUF model in a locked-down container (POST /weights/{id}/serve, /chat)
  auth.py          register / login / me, argon2 password hashes, JWT bearer tokens, login throttle (POST /auth/*)
  db.py            database URL + SQLAlchemy base (DATABASE_URL, defaults to the docker-compose Postgres)
  models.py        schema: users, models, test_sets, training_logs, reports, chat_sessions, chat_messages
  migrations/      Alembic migrations (alembic.ini points at them)
  weights_fixtures.py   builders for (deliberately broken) weight files used by the tests
  test_*.py        pytest tests
  Dockerfile, requirements.txt, requirements-dev.txt   the API image; runtime vs. test dependencies
e2e/               end-to-end tests of the whole Docker stack (run.py, test_stack.py, a stub model)
docker-compose.yml the whole stack: PostgreSQL, API, web
```

Run the backend:

```bash
docker compose up -d --wait db          # only PostgreSQL, on 127.0.0.1:5432 (needs Docker Desktop running)
cd backend
pip install -r requirements-dev.txt     # requirements.txt is the runtime subset the Docker image uses
python -m alembic upgrade head          # create/upgrade the tables
uvicorn main:app --reload --port 8001   # the web app runs on :3000
pytest                                  # schema tests also run on Postgres when it is up
TEST_ON_POSTGRES=1 pytest               # the whole suite on real Postgres (slower, ~2 min)
```

**Sign-in and isolation:** every route except `/health` and `/auth/register|login` requires a
bearer token, and every attached model, test set and training log belongs to the account that
created it. Another account's id answers `404`, the same as an id that doesn't exist.
Third-party API keys are stored encrypted. In development, `JWT_SECRET` and `ENCRYPTION_KEY`
are generated on first run into `backend/.dev-secrets/` (git-ignored) so sign-ins and stored
keys survive restarts; **in production set both as environment variables** and keep the
encryption key safe: lose it and stored keys can't be read (those models must be re-attached).
Tokens last 12 hours. Five failed logins for one email from one address lock that pair out for
15 minutes (in memory, per process; behind a proxy the "address" is the proxy's).
Endpoints, test sets, training logs and uploaded-model records now live in PostgreSQL. Saved chat history is still
kept in the browser (cleared on sign-out); the `chat_*` and `reports` tables are not used yet.
Use `127.0.0.1`, not `localhost`, in `DATABASE_URL` on
Windows: `localhost` tries IPv6 first and each connection waits ~8 s before falling back.
After changing `models.py`: `python -m alembic revision --autogenerate -m "what changed"`,
review the file, then `python -m alembic upgrade head`; `python -m alembic check` fails if
the models and migrations disagree.

## Run everything with Docker

```bash
docker compose up -d --build --wait     # PostgreSQL + API + web; the first build takes a few minutes
```

Then open http://localhost:3000 (the API is on http://localhost:8001). Nothing needs configuring:
the API creates its database tables on start (`alembic upgrade head`), and generates its signing
and encryption keys once, keeping them in a Docker volume so sign-ins and stored API keys survive
restarts and rebuilds. `docker compose down` stops it and keeps your data; add `-v` to delete the
database, uploads and keys too. Everything is published on `127.0.0.1` only.

| Setting (shell or a `.env` file next to `docker-compose.yml`) | Default | Meaning |
|---|---|---|
| `WEB_PORT`, `API_PORT`, `DB_PORT` | 3000, 8001, 5432 | host ports |
| `JWT_SECRET`, `ENCRYPTION_KEY` | generated, kept in the `secrets` volume | set them to manage the keys yourself |
| `MODELHUB_ENV` | empty | `production` turns off the API docs pages |
| `ALLOW_PRIVATE_ENDPOINTS` | empty | `1` lets you attach a model on a private address (e.g. Ollama on the host) |
| `CORS_ORIGINS` | the web app's address | comma-separated browser origins allowed to call the API |
| `NEXT_PUBLIC_API_BASE` | `http://localhost:<API_PORT>` | the API address the *browser* uses; baked in at **build** time, so rebuild after changing it |

Both containers run as a non-root user with a read-only filesystem, all Linux capabilities dropped,
and `no-new-privileges`; the API may write only to its two volumes (uploads, keys) and `/tmp`.

**Running uploaded models does not work inside this stack.** Starting a model needs the API to
control Docker, which means mounting the Docker socket into it, and that is root on your machine.
It is deliberately not done: starting a model answers "Docker is not installed on the server" (HTTP 503)
here. To run uploaded models, run the API on the host (`docker compose up -d --wait db`, then
`uvicorn` as above). Day 29 moves container control into a small separate service.

Changing a dependency: `web/package-lock.json` must install on Linux too, because the image runs
`npm ci`. If it fails with "Missing: ... from lock file", regenerate the lock inside Linux:
`docker run --rm -v "$PWD/web:/w" -w /w node:22-alpine npm install --package-lock-only --ignore-scripts`.

## End-to-end tests

```bash
python e2e/run.py          # build, start, test, and remove the whole stack (about a minute)
python e2e/run.py --keep   # leave it running afterwards;  -k journey  selects tests by name
```

Needs Docker and `pip install -r backend/requirements-dev.txt`. The run uses its own compose
project and ports (web 13000, API 18001, database 15432), so it never touches your development
stack or its data. A small stub model (`e2e/stub_model.py`, standard library only) stands in for a
real LLM, so the expected accuracy is exact. [e2e/test_stack.py](e2e/test_stack.py) checks, over
real HTTP against real containers and PostgreSQL:

| Area | What is checked |
|---|---|
| Stack | every page is served; the web bundle was built with the API's address; migrations ran on an empty database; CORS allows the web app and nobody else; protective headers |
| Journey | register, sign in, attach a model (key accepted, stored, never returned), upload a test set, evaluate (accuracy exactly 18/20), leakage, training log, estimate, readiness, chat plain and streamed |
| Isolation | a second account gets `404` for every id of the first one's endpoint, test set, log and weights, and cannot use or change them |
| Uploads | a genuine safetensors file is accepted and deleted; pickles and torch-style zips are refused under bad and good names and nothing is stored; starting a GGUF model without Docker access answers `503` and the API stays healthy |
| Operations | the API container is replaced and sign-ins, stored API keys and uploads survive (only volumes and the database carry over); containers run non-root, read-only, without the Docker socket |

The suite skips itself when no stack is running, so `pytest` in `backend/` is unaffected. What it
does not cover: clicking through the pages in a real browser (the pages are checked to be served,
and the UI logic by the 60 web unit tests), a real LLM, and real model serving, which needs the
Docker access described above (that path has its own opt-in suite, `test_sandbox_docker.py`).

## MVP walkthrough

1. Start the database and backend (see "Run the backend"; `uvicorn main:app --port 8001`; add `ALLOW_PRIVATE_ENDPOINTS=1` to
   attach a model on localhost, e.g. Ollama) and the web app (`cd web && npm run dev`, then open http://localhost:3000).
2. **Sign in:** create an account (top right); everything below is private to it.
   **Attach:** name, OpenAI-compatible chat URL, model, optional key → it is probed and listed.
3. **Test set:** upload a CSV, pick the target column (needs 2–50 distinct values), optionally
   upload the training CSV to see the train/test overlap warning.
4. **Evaluate:** choose the endpoint, press Evaluate → the report fills with real metrics.
   Up to 200 rows are scored (a fixed random sample above that). Replies naming no single class
   count as wrong. ROC-AUC shows n/a because chat endpoints return labels, not probabilities.
5. **Chat:** open the Chat page and pick the model; replies stream in and history survives a reload.
6. **Training log:** upload a log CSV or import an MLflow run → the loss curve is drawn from it,
   the "training still needed" card shows an estimate with a range, and the assumptions behind it.
7. **Readiness:** the score and the advice list update after each step. Without an evaluation
   there is no score; with only some signals, the verdict says "based on N of 5".

Known limits: the test set, evaluation and training log you were working on survive a page
reload but only within the same browser tab (they are kept in sessionStorage, and emptied when
you sign out or switch account); evaluation runs inside the request; Weights & Biases is supported through its CSV export
(Run → Export), not a direct API import.

## Uploaded model weights

`POST /weights?name=...&filename=...` takes the file as the raw request body (streamed to disk,
never held in memory) and accepts **only `.safetensors` and `.gguf`**. Configure with
`UPLOAD_DIR` (default `backend/.uploads`, git-ignored), `MAX_UPLOAD_GB` (default 2) and
`MAX_USER_UPLOAD_GB` (default 10). The size is checked from `Content-Length` before any of the
body is read, and again while it streams.

What the scan ([scan.py](backend/scan.py)) does: it never loads or executes the model. It reads
only the header, with every length bounded by the file size, and rejects a file that is not what
it claims to be: a pickle, ZIP (`.pt`) or HDF5 file renamed to look harmless is recognised by
its first bytes; safetensors must have exact, gap-free tensor offsets, matching shapes and
dtypes, and no duplicate keys; GGUF must have sane counts, aligned in-bounds tensors and no
repeated keys. It is checked against files written by the real `safetensors` and `gguf`
libraries, plus fuzzed corruption. Files are stored under `<UPLOAD_DIR>/<user id>/<model id>.<ext>`:
the path is built from ids only, never from the uploaded file name.

What it does **not** do: a file that passes is structurally valid, not proven harmless. It says
nothing about whether the weights behave well, and a GGUF `chat_template` is a Jinja program
that must only ever be rendered in a sandbox (the scan warns when one is present). Uploaded
models can then be run in a sandbox (next section).

**Pickle-based files are refused, not sandboxed.** `.pkl`, `.pt`, `.pth`, `.bin`, `.ckpt`,
`.joblib`, `.h5`/`.keras`, `.npy`/`.npz`, `.onnx`, archives and programs all get a specific
refusal that says what the file is and how to convert it (load it on *your own* machine, e.g.
`torch.load(path, weights_only=True)`, then `safetensors.torch.save_file`). Sandboxing was the
alternative, but a sandbox only limits the damage from code the attacker already got to run,
while a refusal costs the user one conversion. Two independent gates apply: the file name
(judged the way Windows would: case-insensitive, trailing dots and spaces ignored, last
extension decides) and the content, which is what really decides, because a name proves
nothing. A file is accepted only if it parses as well-formed safetensors/GGUF, which no pickle,
ZIP or archive can; recognising "this looks like a pickle" only chooses the error message. GGUF
files may carry nothing after the last tensor except zero padding. Nothing in the codebase
imports a deserialiser or evaluates code (a test reads every source file and fails if one does).

Known limits: one request per file (no chunked/resumable upload yet); deleting an *account*
removes its database rows but not its files; two uploads started at the same moment can
overshoot the per-user quota slightly.

## Running uploaded models (sandbox)

`POST /weights/{id}/serve` starts a GGUF model in its own Docker container (llama.cpp's
`llama-server`); `POST /weights/{id}/chat` talks to it (plain or streamed, same event format as
endpoint chat); `DELETE /weights/{id}/serve` stops it. The Connect page has Start / Stop, and a
running model appears in the Chat page's model list. Safetensors files are stored but **not
served yet** (that needs a heavy Python/GPU runtime that doesn't fit this sandbox).

The model file is untrusted input to a C++ parser, so the container is there to contain a bug
in that parser. Every container is created with ([sandbox.py](backend/sandbox.py)):

| Protection | How |
|---|---|
| No network at all | `--network none`. There is no port to publish; the backend talks to the server by running `curl` *inside* the container with `docker exec`, the prompt going in on stdin |
| Nothing writable | read-only root filesystem; a 64 MB `noexec` tmpfs; the weights mounted read-only |
| No privileges | runs as `nobody`, all capabilities dropped, `no-new-privileges` |
| Bounded resources | memory (swap disabled), CPUs, process count, log size |
| No Jinja | this build of `llama-server` runs a model's *embedded* chat template through a Jinja engine by default; we pass `--no-jinja` and a fixed built-in template chosen from an allow-list (the "Chat format" menu) |
| No web UI | `--no-webui` |
| Pinned code | the image is referenced by digest and is never pulled implicitly (`--pull never`) |

Operating limits: one running model per user and two overall by default; at most two
simultaneous chats per model (more get `429`); the generation length is capped by the server
(`SERVE_MAX_TOKENS`), not by the caller; a request that runs too long is killed; a model nobody
uses for 10 minutes is stopped. Containers are labelled, and on every start the backend removes
any left over from a crash. Deleting an uploaded model stops its container first.

Setup: install Docker, then `docker pull` the image named by `LLAMA_IMAGE`
(default `ghcr.io/ggml-org/llama.cpp@sha256:10a38e6c...`, see `backend/config.py`; update the pin
deliberately). Settings: `SERVE_MEMORY` (2g), `SERVE_CPUS` (2), `SERVE_CONTEXT` (2048),
`SERVE_MAX_TOKENS` (512), `SERVE_IDLE_SECONDS` (600), `SERVE_MAX_RUNNING` (2),
`SERVE_MAX_PER_USER` (1), `SERVE_START_TIMEOUT` / `SERVE_REQUEST_TIMEOUT` (120 s), `DOCKER_BIN`.

Tests: the container flags, Docker failure handling, lifecycle, limits and isolation run
everywhere with Docker faked. The real thing (Docker really applies the flags, the model
really answers, the container really cannot reach the internet or write to its filesystem) runs
when you opt in:

```bash
TINY_GGUF=/path/to/stories260K.gguf pytest test_sandbox_docker.py   # a 1 MB test model from llama.cpp
```

Known limits: the backend needs access to the Docker socket, which is root-equivalent on the
host, so it must be trusted and the machine dedicated (the Docker Compose stack therefore does
not mount it, so serving is off there); for deployment the container control should move into a
small separate service (Day 29). A sandbox contains a parser bug, it does not
make a malicious model's *output* trustworthy. Scoring uploaded models with a test set is not
wired up yet (the chat route is). Running containers belong to one backend process, so run a
single worker.

## Security tests

`pytest test_security.py` ([test_security.py](backend/test_security.py)) is the safety net for
uploads and the API surface. Its centrepiece is a *real* payload, a pickle that writes a marker
file if anything unpickles it; a control test proves it works, and every other test proves it
was never run.

| Risk | Control | Tested by |
|---|---|---|
| A pickle uploaded as `.pkl` / `.pt` / `.bin` / `.joblib` / ... | refused by extension, with the reason and how to convert | every payload (pickle protocols 0-5, torch-style zip, gzip, tar, numpy object array) under 20+ refused names, including case, trailing-dot and double-extension tricks |
| The same pickle renamed `.safetensors` / `.gguf` | refused by content: it does not parse as the format | the same payloads under good names; the scanner is also run on them directly |
| Code run by the scanner or anywhere else | nothing deserialises or evaluates; `pickletools` is used only to *disassemble* | AST test over every source file; a self-test of that checker; marker file never appears |
| A real model rejected by mistake | acceptance is decided by structure, never by a signature guess | regression for a real Day 25 bug (safetensors headers of 640/896/1152/1408 bytes start like a pickle and were refused); every header length 64-8192 |
| A payload hidden in a valid file | GGUF: only zero padding after the last tensor; safetensors: no trailing bytes | payloads appended to GGUF; files from the real `gguf` library still pass |
| Filename tricks (path traversal, NUL, bidi/zero-width characters, `?` `#` `%2e`) | files are stored as `<user id>/<model id>.<ext>`; display names are stripped of control and invisible characters | 18 names through the API |
| A decompression bomb | archives are never unpacked | a 50 MB-of-zeros zip is refused in well under a second |
| Scanner denial of service | every length is bounded by the file size | 100k-tensor safetensors, 50k-entry GGUF, absurd claims, pathological pickle-like input: all bounded in time |
| Browser-side attacks on API replies | `nosniff`, `no-store`, `no-referrer`, `DENY`, a `default-src 'none'` CSP | headers on success and error replies; a header a route sets itself (SSE `no-cache`) is kept |
| The API docs exposing the surface | off when `MODELHUB_ENV=production` | checked in a fresh process |
| Secrets committed | `.dev-secrets/`, `.uploads/`, `.env` are git-ignored | read from `.gitignore` |

Earlier days' controls are tested where they were built: the container flags and the real-Docker
suite (`test_sandbox*.py`), the isolation tests (`test_isolation.py`, including a sweep that fails
if any route lacks authentication), and the scanner tests with fuzzed corruption (`test_scan.py`).

## Target stack

| Layer | Choice | Why |
|---|---|---|
| Frontend | React / Next.js + Tailwind | matches the design system; current HTML/CSS/JS is a stepping stone, migrate once the backend has real data to render |
| Charts | Recharts or Plotly | loss curves, progress cards |
| Chat UI | Streaming component (SSE or WebSockets) | model replies should stream, not appear all at once |
| File upload | Chunked / resumable | model files can be several GB |
| Backend API | FastAPI (Python) | stays close to the ML libraries doing the analysis |
| Task queue | Celery or RQ + Redis | analysis jobs take minutes, not milliseconds — can't block the request |
| Database | PostgreSQL | users, models, reports, chat history |
| Object storage | S3 or MinIO | uploaded model files and datasets |
| Auth | Sign-in + per-user isolation | one person must not see another's models |

## Analysis engine — the hard part

What can be measured depends entirely on the model type. A model file alone **cannot**
say "how much it's trained" — that needs training logs or checkpoints. "How much more
training is needed" comes from fitting the loss/validation curves and finding where they
flatten. It's an estimate with assumptions stated, not a guarantee — there's no true
"perfect" point, only diminishing returns.

| Model type | What we can measure | What the user must provide |
|---|---|---|
| Classical ML (sklearn, XGBoost) | Accuracy, F1, ROC-AUC, confusion matrix | A test dataset + target column |
| Deep learning (PyTorch, TF) | The same, plus overfitting checks | A test dataset + training logs for loss curves |
| LLMs | Benchmark scores, perplexity, custom evals | An eval set or a benchmark choice (e.g. via `lm-evaluation-harness`) |

Libraries to build on: **scikit-learn** / **evaluate** for metrics, **lm-evaluation-harness**
or **DeepEval** for LLMs, and an **MLflow** / **Weights & Biases** import so users can bring
existing training history instead of re-logging it.

**Decision for v1: support LLMs and tabular classification only.** Supporting every model
type at once is what sinks projects like this.

## Running models for chat — the risky part

An uploaded model file can contain arbitrary code, so this has to be treated as untrusted
input:

- **Pickle-based formats run code on load.** `.pkl`, `.pt`, `.pth`, `.bin`, `.ckpt`,
  `.joblib` can all execute arbitrary Python when deserialized — a `.pt` file is literally
  a zip of pickle bytecode. `safetensors` is data-only and safe by design. "Safe" formats
  still need care: ONNX can be crafted for arbitrary file read/write via external data
  paths, and Keras/HDF5 Lambda layers can run code on load too.
- **v1 plan: accept `safetensors`, ONNX and GGUF.** Avoid or heavily sandbox `.pkl`/`.pt`
  uploads. Scan files before loading (e.g. [TensorTrap](https://pypi.org/project/tensortrap)).
- **Serving:** vLLM or llama.cpp for LLMs, Triton or TorchServe for others.
- **Isolation:** each model runs in a container — no network, CPU/RAM/time limits.
- **Endpoint mode comes first.** If a user connects an API endpoint instead of uploading
  a file, we run nothing ourselves. Much cheaper and much safer — this is the MVP path.
- **Chat orchestration:** LangChain/LangGraph to route messages, keep history, and call
  the right backing model.

## Infrastructure (later)

Docker for everything; a GPU-capable cloud host (AWS/GCP/RunPod) once we self-host model
serving; monitoring/logs; per-user rate limits and quotas.

## Build order

1. **MVP:** connect-an-endpoint flow only. Upload a test set, compute accuracy/metrics,
   chat through the endpoint. No file uploads, no GPUs, no sandboxing yet.
2. **Next:** upload training logs → real loss-curve chart and a real "training still
   needed" estimate (replacing the placeholder numbers in the sample report).
3. **Then:** file upload with sandboxed serving, starting with `safetensors` and GGUF LLMs.
4. **Later:** payments, teams, report sharing.

## Roadmap — 30-day schedule

Fixed deadline: **Day 30 = 2026-11-08** (Day 1 = 2026-10-10). One task per day, with a
buffer day at the end of each phase. If a day slips, absorb it in the buffer — don't move
the end date, and don't pull work forward.

**Phase 1 — MVP: connect an endpoint, get metrics, chat (Days 1–11)**

- [x] Day 1 (Oct 10): static landing page
- [x] Day 2 (Oct 12): FastAPI backend with `/health`
- [x] Day 4 (Oct 13): endpoint-connect API — register and validate an API endpoint
- [x] Day 5 (Oct 14): wire the frontend connect form to the endpoint API
- [x] Day 6 (Oct 15): test-set upload (CSV), target-column selection, train/test leakage warning
- [x] Day 7 (Oct 16): classification metrics — accuracy, F1, ROC-AUC, confusion matrix
- [x] Day 8 (Oct 17): run the test set through the endpoint; report page shows real metrics
- [x] Day 9 (Oct 18): chat through the connected endpoint (non-streaming)
- [x] Day 10 (Oct 19): stream chat replies (SSE) and chat history in the UI
- [x] Day 11 (Oct 20): buffer — tests, bug fixes, MVP walkthrough

**Phase 2 — training analysis (Days 12–17)**

- [x] Day 12 (Oct 21): upload training logs (raw CSV) and parse them
- [x] Day 13 (Oct 22): real loss-curve chart
- [x] Day 14 (Oct 23): MLflow / W&B import (MLflow via REST; W&B via its CSV export, no direct API import yet)
- [x] Day 15 (Oct 24): "training still needed" estimate from curve-fitting
- [x] Day 16 (Oct 25): estimate ranges, stated assumptions, and UI
- [x] Day 17 (Oct 26): readiness score + "what to do next" from real metrics

**Phase 3 — accounts and data (Days 18–21)**

- [x] Day 18 (Oct 27): PostgreSQL schema for users / models / reports / chat
- [x] Day 19 (Oct 28): sign-in / auth
- [x] Day 20 (Oct 29): per-user isolation + isolation tests
- [x] Day 21 (Oct 30): buffer — tests, bug fixes

**Phase 4 — frontend migration (Days 22–24)**

- [x] Day 22 (Oct 31): Next.js + Tailwind scaffold
- [x] Day 23 (Nov 1): migrate landing, connect, and report pages
- [x] Day 24 (Nov 2): migrate chat and charts; retire the static frontend

**Phase 5 — model file upload and hardening (Days 25–30)**

- [x] Day 25 (Nov 3): weight upload (`safetensors` / GGUF only) with file scanning
- [x] Day 26 (Nov 4): sandboxed serving — container, no network, resource limits
- [x] Day 27 (Nov 5): reject or sandbox `.pkl` / `.pt`; security tests
- [x] Day 28 (Nov 6): Docker Compose for the whole stack; end-to-end tests
- [ ] Day 29 (Nov 7): deployment
- [ ] Day 30 (Nov 8): final polish, docs, release

Payments, teams, and report sharing are out of scope for the 30 days.

## Research & prior art

Nothing combines all four pieces of this product yet, but each piece has prior work worth
reading before building on it:

- **[Deepchecks](https://github.com/deepchecks/deepchecks)** — Python library for
  validating models/data: predictive performance, data integrity, distribution mismatches.
  **AGPL-licensed** — check with a lawyer before depending on it inside a hosted product.
  Paper: [JMLR](https://www.jmlr.org/beta/papers/v23/22-0281.html).
- **[Giskard](https://landscape.jimmysong.io/projects/giskard-oss/)** — open-source
  scanner for performance/bias/security issues in LLM and traditional ML models, with a
  web UI.
- **[lm-evaluation-harness](https://github.com/EleutherAI/lm-evaluation-harness)**
  (EleutherAI) — backend for Hugging Face's Open LLM Leaderboard, 60+ standard
  benchmarks, loads models via transformers, vLLM, or API. This is the likely LLM-eval
  dependency for this project.
- **MLflow** / **Weights & Biases** — where users' existing training history will most
  often come from; building an importer beats asking users to re-log everything.

**On "how much more training is needed" (the hardest feature — mostly about data, not epochs):**
- Johnson et al., ACL 2018 — [aclanthology.org/P18-2072](https://aclanthology.org/P18-2072/)
  — predicts training-data needs by extrapolating from small pilot subsets; shows the
  extrapolation model and hyperparameters chosen can swing the result a lot.
- [arxiv.org/pdf/2307.00374](https://arxiv.org/pdf/2307.00374) — compares simple
  parametric learning-curve models (and an ensemble) for predicting performance from a
  few observed points.
- Chinchilla scaling laws (Hoffmann et al. 2022) — ~20 training tokens per parameter is
  compute-optimal for pretraining. Explainers:
  [Analytics Vidhya](https://www.analyticsvidhya.com/blog/2024/09/chinchilla-scaling-law/),
  [theorempath breakdown](https://theorempath.com/papers/chinchilla) (also links a
  follow-up on inference cost, arXiv:2401.00448, and a replication attempt,
  arXiv:2404.10102). These describe **pretraining** — treat as a guide, not a measure of
  whether a fine-tuned model is "done".

**Suggested reading order:** Johnson et al. 2018 → Deepchecks paper + lm-evaluation-harness
README (to see what a good report contains) → a Chinchilla explainer → pickle-vs-safetensors
security material (below), before writing any upload code.

## Things to not forget

1. **Pickle files can run code on load** — see the security section above. Don't skip the
   scan step to save time.
2. **"Perfect" doesn't exist.** Every estimate needs a stated range and stated assumptions
   (which curve was fit, on what data).
3. **The test set decides accuracy.** Warn users about data leakage / train-test overlap.
4. **Reproducibility.** Store benchmark version, prompts, and settings with every report
   so scores stay comparable across runs — lm-evaluation-harness does this and it's worth
   copying.
5. **Licenses.** Check every library's license before depending on it (Deepchecks is AGPL),
   and the license of every model a user uploads.

## Dev log

| Date       | What was done |
|------------|---------------|
| 2026-10-10 | Project created; static landing page built from the design mockup; researched prior art and wrote the full build plan (stack, analysis engine, security, roadmap) into this README |
| 2026-10-12 | Added FastAPI backend with `GET /health`, CORS for the local frontend, and pytest tests |
| 2026-10-13 | Added endpoint-connect API (`/endpoints`): probes an OpenAI-compatible chat URL, blocks private addresses, keeps API keys server-side; in-memory store until the DB day |
| 2026-10-13 | Replaced the open-ended roadmap with a fixed 30-day schedule ending 2026-11-08 |
| 2026-10-14 | Wired the frontend attach form to `POST /endpoints`: name/URL/model/API key fields, status messages, a live attached-endpoints list with remove; verified end to end against a local mock endpoint |
