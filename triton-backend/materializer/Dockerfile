# Build with an explicitly resolved Python base digest; there is no fabricated default.
ARG PYTHON_BASE_IMAGE
FROM ${PYTHON_BASE_IMAGE}
WORKDIR /opt/materializer
COPY triton-backend/materializer/requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY triton-backend/app ./app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 AWS_EC2_METADATA_DISABLED=true
USER 10001:10001
ENTRYPOINT ["python", "-m", "app.services.materialization.cli"]
