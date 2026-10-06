# Container build for the support agent (Agent Manager "docker" build type).
#
# Why not the buildpack build: Google's buildpack builder image is published for amd64 only. On an ARM64 cluster
# (Apple Silicon, Graviton) its lifecycle runs under emulation and crashes ("fatal error: lfstack.push").
# python:3.12-slim is multi-arch, so this builds natively on both amd64 and arm64.
#
# Tracing: a buildpack-built Python agent gets auto-instrumentation from an init container. A Docker-built agent only
# gets AMP_OTEL_ENDPOINT and AMP_AGENT_API_KEY injected, so the image brings amp-instrumentation itself and starts
# the agent under amp-instrument. The evaluation monitors score these traces.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt "amp-instrumentation==0.4.1"

COPY *.py ./

# Agent Manager's Chat Agent contract: POST /chat on port 8000.
EXPOSE 8000
USER 10001
CMD ["amp-instrument", "python", "main.py"]
