# Math Olympiad Mentor

A Grade 6 Maths Olympiad coach that uses a loaded textbook (PDF) to explain concepts and run 5-question multiple-choice quizzes. Pass at 60% (3/5) to mark a concept as fully understood.

## Quick start

### GUI (desktop)

```bash
cd openai/math-olympiad-mentor
pip install -r requirements.txt
python app_gui.py
```

Put PDFs in the `books/` folder so the coach can load them. Uses **Ollama** by default (model `gpt-oss:latest`). To use OpenAI instead, set `USE_OLLAMA=0` and `OPENAI_API_KEY`. See [Using Ollama](docs/using-ollama.md).

### Web app

```bash
cd openai/math-olympiad-mentor
pip install -r requirements-web.txt
python web_app.py
```

Then open **http://localhost:8000**. Or run with uvicorn:

```bash
uvicorn web_app:app --host 0.0.0.0 --port 8000
```

See [README-WEB-DEPLOY.md](README-WEB-DEPLOY.md) for Docker and Kubernetes.

---

## Features

- **Concepts from book** – Select a concept; get an explanation or start a quiz.
- **Quiz flow** – Coach explains the concept (with “Important things to remember”), then you click **Ready** (or type READY) to start 5 multiple-choice questions. Questions are about the selected concept from the book. After each answer you get Correct/Incorrect and the next question. Score and “fully understood” status at the end (pass ≥60%).
- **Ready button** – After the explanation, use the **Ready** button instead of typing READY (GUI and web).
- **Persistent memory** – Notes about the student are saved across sessions (GUI).

## Environment

| Variable | Purpose |
|----------|--------|
| `OPENAI_API_KEY` | OpenAI API key (when `USE_OLLAMA=0`). |
| `USE_OLLAMA=1` or `OLLAMA_BASE_URL` | Use Ollama (default). See [docs/using-ollama.md](docs/using-ollama.md). |
| `OLLAMA_MODEL` | Ollama model (default `gpt-oss:latest`). |

## Project layout

- `agent.py` – Coach agent: `get_reply(history, pdf_text=..., phase_instruction=...)`
- `app_gui.py` – Tkinter GUI (concepts, chat, quiz, Ready button)
- `web_app.py` – FastAPI web app (same flow, browser UI)
- `books/` – Put PDF (and .txt) files here; loaded on startup
- `k8s/` – Kubernetes manifests for deployment
- `docs/` – [Architecture & flow](docs/architecture-and-flow.md), [Using Ollama](docs/using-ollama.md)
