# Math Olympiad Coach – Web App & Kubernetes Deployment

For general run instructions (GUI and web), see [README.md](README.md).

## Web app (local)

```bash
cd openai/math-olympiad-mentor
pip install -r requirements-web.txt
# Uses Ollama by default (gpt-oss:latest). Ensure Ollama is running. Or set USE_OLLAMA=0 and OPENAI_API_KEY for OpenAI.
uvicorn web_app:app --host 0.0.0.0 --port 8000
# Open http://localhost:8000
```

- **GET /** – Chat UI  
- **GET /health** – Health check  
- **GET /api/concepts** – List concepts from loaded book  
- **POST /api/chat** – Send message, get coach reply (body: `{"message": "...", "history": [...]}`)

Books are loaded from the `books/` directory on startup.

---

## Docker

```bash
cd openai/math-olympiad-mentor
docker build -t math-olympiad-coach:latest .
docker run -p 8000:8000 math-olympiad-coach:latest
```

Uses Ollama (gpt-oss:latest) by default. If Ollama runs on the host, set `OLLAMA_BASE_URL=http://host.docker.internal:11434` (Mac/Windows) or `http://172.17.0.1:11434` (Linux). For OpenAI: `docker run -p 8000:8000 -e USE_OLLAMA=0 -e OPENAI_API_KEY=your-key math-olympiad-coach:latest`.

---

## Kubernetes

### Apply manifests (after building and pushing the image)

1. Set the image in `k8s/deployment.yaml` to your registry image (e.g. `ghcr.io/<owner>/math-olympiad-coach:latest`), or use the CI/CD workflow to do it.
2. Ensure Ollama is reachable (default; set `OLLAMA_BASE_URL` in k8s if needed). Or set `USE_OLLAMA=0` and `OPENAI_API_KEY` for OpenAI.
3. Apply:

```bash
kubectl apply -f openai/math-olympiad-mentor/k8s/
# Optional: Ingress for external access
kubectl apply -f openai/math-olympiad-mentor/k8s/ingress.yaml
```

4. Port-forward to test: `kubectl port-forward svc/math-olympiad-coach 8000:80`

---

## CI/CD (GitHub Actions)

The workflow **Build and Deploy** runs from the **repository root** (e.g. `mcp-learning/.github/workflows/build-and-deploy.yml`):

1. **Build and push** – On push to `main`/`master`: builds the Docker image from `openai/math-olympiad-mentor/Dockerfile` and pushes to GitHub Container Registry (`ghcr.io/<owner>/math-olympiad-coach:latest`).
2. **Deploy** – Applies `k8s/` to the cluster and runs a rollout.

### Setup for deploy

1. **Secrets**
   - **KUBECONFIG** – kubeconfig for the target cluster (contents or base64-encoded). The workflow writes this to `~/.kube/config` so `kubectl` can run.

2. **Optional**
   - **Environment** – The deploy job uses the `production` environment. Create it in the repo settings if you want approval gates.
   - **Variable** – `K8S_NAMESPACE`: namespace to deploy into (default: `default`).

3. **Image pull (for private registry or GHCR)**
   - If the image is private, create an image pull secret in the cluster and add `imagePullSecrets` to the Deployment.

### Disabling deploy

- Remove or skip the `deploy` job, or
- Delete the `KUBECONFIG` secret so the deploy step fails (and use `continue-on-error: true` to keep the workflow green), or
- Use branch protection so the workflow only runs on manual dispatch.

### Running only build (no deploy)

Push to a branch other than `main`/`master`, or trigger the workflow manually and rely on the deploy job’s `if` so it only deploys from `main`/`master`.
