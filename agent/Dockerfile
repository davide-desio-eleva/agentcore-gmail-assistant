# AgentCore Runtime requires an ARM64 container that listens on port 8080.
# Pinned by digest, so the base image changes only when this line does. Update it on purpose: docker buildx imagetools inspect <tag>.
FROM --platform=linux/arm64 public.ecr.aws/docker/library/python:3.13-slim@sha256:ae14d4ce8771606477a81446e7ef429edd6762305fc50c84507c1041fa8285d1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.txt

COPY main.py .
COPY gmail_assistant/ gmail_assistant/

# Nothing here needs root, so the runtime does not run as root.
RUN useradd --system --no-create-home --uid 10001 agent
USER agent

EXPOSE 8080
CMD ["python", "main.py"]
