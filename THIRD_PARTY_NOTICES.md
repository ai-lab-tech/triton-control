# Third-Party Notices

This project is licensed under the Apache License 2.0. Some included or
generated files retain their own upstream notices and license terms.

## NVIDIA Triton Inference Server protobuf definitions

Files under `triton-backend/protobuff/` include protobuf definitions derived
from NVIDIA Triton Inference Server releases. Those files contain upstream
copyright and BSD-style redistribution notices from NVIDIA CORPORATION &
AFFILIATES and TensorFlow Authors. Keep those notices with source and binary
redistributions that include the protobuf definitions or generated code derived
from them.

## Runtime and build dependencies

Runtime and build dependencies are not relicensed by this project. Their own
license terms apply.

Because dependency versions can change over time, treat the dependency metadata
and lockfiles in this repository as the source of truth for current third-party
components and versions. In particular:

- frontend dependencies: `triton-frontend/package.json` and
  `triton-frontend/package-lock.json`
- backend dependencies: `triton-backend/pyproject.toml`

When preparing a release, regenerate and review the current third-party
dependency/license inventory from those files.
