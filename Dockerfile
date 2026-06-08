# syntax=docker/dockerfile:1.7

FROM node:22-alpine AS frontend-build
WORKDIR /src/frontend

RUN apk add --no-cache bash curl openjdk17-jre python3

COPY triton-frontend/package*.json ./
RUN npm ci

COPY triton-frontend/ ./
RUN sed -i 's/\r$//' scripts/generate-api.sh \
    && npm run generate:api

# In the combined Kubernetes image the browser talks to the same ingress host.
# Empty apiBaseUrl makes generated API calls relative to the current origin.
RUN node -e "const fs=require('fs'); const p='src/environments/environment.ts'; let s=fs.readFileSync(p,'utf8'); s=s.replace(/apiBaseUrl:\\s*['\"][^'\"]*['\"]/, 'apiBaseUrl: \"\"'); fs.writeFileSync(p,s);"
RUN node -e "const fs=require('fs'); const p='angular.json'; let s=fs.readFileSync(p,'utf8'); s=s.replace(/\"maximumWarning\":\\s*\"500kb\"/, '\"maximumWarning\": \"2mb\"').replace(/\"maximumError\":\\s*\"1mb\"/, '\"maximumError\": \"3mb\"'); fs.writeFileSync(p,s);"
RUN npm run build -- --configuration production

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    PYTHONPATH=/opt/triton-backend \
    BACKEND_HOST=0.0.0.0 \
    BACKEND_PORT=8000

RUN apt-get update \
    && apt-get install -y --no-install-recommends nginx ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && rm -f /etc/nginx/sites-enabled/default

RUN groupadd --system --gid 10001 triton \
    && useradd --system --uid 10001 --gid triton --home-dir /nonexistent --shell /usr/sbin/nologin triton

RUN mkdir -p /tmp/nginx \
    && mkdir -p /var/lib/nginx/body /var/lib/nginx/proxy /var/lib/nginx/fastcgi /var/lib/nginx/uwsgi /var/lib/nginx/scgi \
    && chown -R 10001:10001 /tmp/nginx /var/lib/nginx

WORKDIR /opt/triton-backend

COPY triton-backend/ /opt/triton-backend/
RUN ln -s /opt/triton-backend/protobuff /opt/triton-backend/app/protobuff
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir /opt/triton-backend

COPY --from=frontend-build /src/frontend/dist/triton-admin/browser/ /usr/share/nginx/html/
COPY docker/nginx.conf /etc/nginx/nginx.conf
COPY docker/proxy_headers.conf /etc/nginx/conf.d/proxy_headers.conf
COPY docker/start-triton-admin.sh /usr/local/bin/start-triton-admin.sh

RUN sed -i 's/\r$//' /usr/local/bin/start-triton-admin.sh
RUN chmod +x /usr/local/bin/start-triton-admin.sh

USER 10001:10001

EXPOSE 8080 8000

CMD ["/usr/local/bin/start-triton-admin.sh"]
