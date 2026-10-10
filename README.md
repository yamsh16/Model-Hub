# Model Hub

Attach a trained AI / ML / LLM model and get a clear report on how much it has trained,
how accurate it is, and how much more training it needs. Then chat with it in the browser.

## Running it

The front end is plain HTML, CSS and JavaScript, so there is no build step.

```bash
cd frontend
python -m http.server 8000
```

Then open http://localhost:8000.

## Project layout

```
frontend/
  index.html   page structure
  styles.css   all styling (colours and fonts are set at the top)
  main.js      drag-and-drop, endpoint form, chat box
```

## Roadmap

- [x] **Day 1:** static landing page (header, hero with attach card, sample report, chat, how it works)
- [ ] Python backend (FastAPI) with a health-check route
- [ ] Upload endpoint: accept model files and store them
- [ ] Read model metadata (layers, parameter count) from `.safetensors` / `.pt` / `.onnx`
- [ ] Read training logs (loss / accuracy per epoch) and draw the loss curve from real data
- [ ] Compute training progress, accuracy gap and readiness score
- [ ] Generate "What to do next" advice from the metrics
- [ ] Connect a Hugging Face repo or API endpoint
- [ ] Live chat with the attached model
- [ ] Tests and deployment

## Dev log

| Date       | What was done |
|------------|---------------|
| 2026-10-10 | Project created; static landing page built from the design mockup |
