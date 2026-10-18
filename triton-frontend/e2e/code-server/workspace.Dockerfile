FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates tar gzip git \
    && rm -rf /var/lib/apt/lists/*
# The production workspace startup installs the selected code-server release
# and the bundled extension. Do not install either through a test-only path.
USER 10001:10001
