# Model Hub

A web app where users attach a trained AI / ML / LLM model, get a report on how much it
has trained, how accurate it is, and how much more training it needs — then chat with it,
right in the browser.

No existing product does all four of these together (attach, report, estimate remaining
training, chat), so this combines a few researched, separately-solved problems into one
tool. See [Research & prior art](#research--prior-art) below for what already exists and
what this project leans on.

## Current state

The front end (plain HTML/CSS/JS, [design mockup](frontend/index.html)) can now attach a
real API endpoint through the backend: fill in name/URL/model/API key, it's probed and
registered server-side, and the attached list is live. The report and chat sections are
still placeholders — that's Day 6+.

```bash
cd frontend
python -m http.server 8000
```

Then open http://localhost:8000. The backend (below) must be running on port 8001 for the
attach form to work.

```
frontend/
  index.html   page structure
  styles.css   all styling (colours and fonts are set at the top)
  main.js      drag-and-drop, endpoint form, chat box
backend/
  main.py          FastAPI app (GET /health)
  endpoints.py     register / list / get / delete API endpoints (POST /endpoints probes the URL)
  test_main.py, test_endpoints.py   pytest tests
  requirements.txt Python dependencies
```

Run the backend:

```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8001   # :8000 is taken by the frontend
pytest
```

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
- [ ] Day 6 (Oct 15): test-set upload (CSV), target-column selection, train/test leakage warning
- [ ] Day 7 (Oct 16): classification metrics — accuracy, F1, ROC-AUC, confusion matrix
- [ ] Day 8 (Oct 17): run the test set through the endpoint; report page shows real metrics
- [ ] Day 9 (Oct 18): chat through the connected endpoint (non-streaming)
- [ ] Day 10 (Oct 19): stream chat replies (SSE) and chat history in the UI
- [ ] Day 11 (Oct 20): buffer — tests, bug fixes, MVP walkthrough

**Phase 2 — training analysis (Days 12–17)**

- [ ] Day 12 (Oct 21): upload training logs (raw CSV) and parse them
- [ ] Day 13 (Oct 22): real loss-curve chart
- [ ] Day 14 (Oct 23): MLflow / W&B import
- [ ] Day 15 (Oct 24): "training still needed" estimate from curve-fitting
- [ ] Day 16 (Oct 25): estimate ranges, stated assumptions, and UI
- [ ] Day 17 (Oct 26): readiness score + "what to do next" from real metrics

**Phase 3 — accounts and data (Days 18–21)**

- [ ] Day 18 (Oct 27): PostgreSQL schema for users / models / reports / chat
- [ ] Day 19 (Oct 28): sign-in / auth
- [ ] Day 20 (Oct 29): per-user isolation + isolation tests
- [ ] Day 21 (Oct 30): buffer — tests, bug fixes

**Phase 4 — frontend migration (Days 22–24)**

- [ ] Day 22 (Oct 31): Next.js + Tailwind scaffold
- [ ] Day 23 (Nov 1): migrate landing, connect, and report pages
- [ ] Day 24 (Nov 2): migrate chat and charts; retire the static frontend

**Phase 5 — model file upload and hardening (Days 25–30)**

- [ ] Day 25 (Nov 3): weight upload (`safetensors` / GGUF only) with file scanning
- [ ] Day 26 (Nov 4): sandboxed serving — container, no network, resource limits
- [ ] Day 27 (Nov 5): reject or sandbox `.pkl` / `.pt`; security tests
- [ ] Day 28 (Nov 6): Docker Compose for the whole stack; end-to-end tests
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
