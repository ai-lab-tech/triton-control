FROM python:3.12-slim
ARG CODE_SERVER_VERSION=4.125.0
ARG ARGO_CLI_VERSION=4.0.6
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates git \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL "https://github.com/coder/code-server/releases/download/v${CODE_SERVER_VERSION}/code-server-${CODE_SERVER_VERSION}-linux-amd64.tar.gz" \
       | tar -xz -C /opt \
    && ln -s "/opt/code-server-${CODE_SERVER_VERSION}-linux-amd64/bin/code-server" /usr/local/bin/code-server
RUN curl -fsSL "https://github.com/argoproj/argo-workflows/releases/download/v${ARGO_CLI_VERSION}/argo-linux-amd64.gz" \
    | gzip -d > /usr/local/bin/argo && chmod 755 /usr/local/bin/argo
WORKDIR /opt/triton-backend
COPY triton-backend/pyproject.toml triton-backend/README.md ./
COPY triton-backend/app/ ./app/
COPY triton-backend/protobuff/ ./protobuff/
COPY code-server-extensions/ /opt/code-server-extensions/
RUN ln -s /opt/triton-backend/protobuff /opt/triton-backend/app/protobuff \
    && pip install --no-cache-dir .
RUN chown -R 10001:10001 "/opt/code-server-${CODE_SERVER_VERSION}-linux-amd64"
ENV PYTHONPATH=/opt/triton-backend PYTHONUNBUFFERED=1
USER 10001:10001
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
