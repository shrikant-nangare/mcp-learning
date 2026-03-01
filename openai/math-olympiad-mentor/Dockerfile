# Math Olympiad Coach – Web app image for Kubernetes
FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt requirements-web.txt ./
RUN pip install --no-cache-dir -r requirements-web.txt

# Application code
COPY agent.py web_app.py ./
COPY books/ ./books/

# Default: use Ollama with gpt-oss:latest (set USE_OLLAMA=0 and OPENAI_API_KEY for OpenAI)
ENV USE_OLLAMA=1
ENV OLLAMA_MODEL=gpt-oss:latest

EXPOSE 8000

# Run with uvicorn (single worker for in-memory state; scale via replicas + sticky session if needed)
CMD ["uvicorn", "web_app:app", "--host", "0.0.0.0", "--port", "8000"]
