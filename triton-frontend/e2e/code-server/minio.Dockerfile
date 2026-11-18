# Build the test dependency from a fixed upstream release: public prebuilt
# community images/binaries are no longer reliably available.
FROM golang:1.24-bookworm AS build
RUN git clone --depth 1 --branch RELEASE.2025-04-22T22-12-26Z https://github.com/minio/minio.git /src
WORKDIR /src
RUN CGO_ENABLED=0 go build -o /minio .

FROM debian:bookworm-slim
COPY --from=build /minio /usr/local/bin/minio
RUN useradd --uid 10001 --user-group --create-home minio \
    && mkdir -p /data && chown 10001:10001 /data
ENV HOME=/home/minio
USER 10001:10001
ENTRYPOINT ["/usr/local/bin/minio"]
