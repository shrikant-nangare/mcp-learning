# Using Ollama (default)

The Maths Olympiad Mentor uses **Ollama** by default (model **gpt-oss:latest**). You can switch to OpenAI by setting `USE_OLLAMA=0` and `OPENAI_API_KEY`.

## 1. Install and run Ollama

- Install from [ollama.ai](https://ollama.ai).
- Start Ollama (it usually runs in the background and serves `http://localhost:11434`).
- Pull a model, e.g.:
  ```bash
  ollama pull gpt-oss:latest
  ```

## 2. Run the app (Ollama is default)

**GUI:**

```bash
python app_gui.py
```

**Web app:**

```bash
uvicorn web_app:app --host 0.0.0.0 --port 8000
```

Ollama with `gpt-oss:latest` is used by default. To use a different model, set `OLLAMA_MODEL`:

```bash
export OLLAMA_MODEL=mistral
python app_gui.py
```

You can also set `OLLAMA_BASE_URL` to override the API URL (e.g. `http://localhost:11434/v1`).

## 3. Summary

| Env var           | Meaning |
|-------------------|--------|
| `USE_OLLAMA=1`    | Use Ollama (default; URL `http://localhost:11434/v1`). |
| `OLLAMA_BASE_URL` | Override Ollama API URL (setting it also enables Ollama). |
| `OLLAMA_MODEL`    | Model name (default `gpt-oss:latest`), e.g. `mistral`, `phi`, `llama3.2`. |

With Ollama, the app uses the **Chat Completions** API. With OpenAI it uses the **Responses** API. No code changes are needed; the agent detects the backend from the environment and client.

### Context limit (4096 tokens)

Many Ollama setups use a 4096-token context limit. To avoid "truncating input prompt" warnings and lost context, the app automatically **when using Ollama**:

- Caps PDF reference text to 8,000 characters (about 2k tokens).
- Sends only the **last 10 messages** of the conversation (5 exchanges).

So the coach still has recent chat and a slice of the book; for long books or long chats, the model may not see the very beginning. To increase the limit, your Ollama server or model must support a larger context (e.g. 8192 or 32k) and you may need to adjust `MAX_PDF_CONTEXT_CHARS_OLLAMA` and `MAX_HISTORY_MESSAGES_OLLAMA` in `agent.py`.
