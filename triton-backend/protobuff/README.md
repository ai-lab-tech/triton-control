
# protobuff

Triton has multiple model_config.proto files, and each release branch has its own version.

For a given Triton version, we need to detect the corresponding release branch from:
https://github.com/triton-inference-server/server/releases?page=2

Example:
- Triton version 2.55.0 corresponds to release branch r25.02.

This folder uses 3 scripts:

1. download_model_config_per_branch.py
- Downloads model_config.proto for each release branch.

2. generate_model_config_pb2.py
- Converts those proto files into model_config_pb2.py.

3. triton_release_map.py
- Maps semver (for example 2.55.0) to release branch (for example r25.02).

## model_config.proto vs model_config_pb2.py

1. model_config.proto
- The source protobuf schema file.
- Human-readable and versioned per Triton release branch.
- Defines messages, fields, and enums (the contract).

2. model_config_pb2.py
- Auto-generated Python code from model_config.proto.
- Contains Python message classes (for example ModelConfig).
- Used by backend runtime to parse and validate config.pbtxt.









