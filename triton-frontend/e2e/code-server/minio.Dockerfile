# Build the test dependency from a fixed upstream release: public prebuilt
# community images/binaries are no longer reliably available.
FROM golang:1.24-bookworm AS build
RUN git clone --depth 1 --branch RELEASE.2025-04-22T22-12-26Z https://github.com/minio/minio.git /src
WORKDIR /src
RUN CGO_ENABLED=0 go build -o /minio .

FROM debian:bookworm-slim
COPY --from=build /minio /usr/local/bin/minio
ENTRYPOINT ["/usr/local/bin/minio"]
