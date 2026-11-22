ARG HELPER
FROM ${HELPER}
USER root
# Expose the contracts from the pinned qualification image to isolated Python.
RUN python -c "import pathlib,site; pathlib.Path(site.getsitepackages()[0], 'fixture.pth').write_text('/opt/materializer\n')"
COPY --chmod=0555 server.py /usr/local/bin/tritonserver
USER 10001:10001
