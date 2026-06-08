# Model Config Validation

Triton Control validates editable `config.pbtxt` files before saving them to
S3. The validation uses the NVIDIA Triton `model_config.proto` schema for the
Triton server version of the selected instance.

## Runtime Behavior

When a user saves `config.pbtxt` in the S3 Browser, the backend:

- detects the Triton server version from instance metadata
- maps the version to the matching Triton release branch
- validates the file with the generated protobuf parser for that branch
- rejects invalid configuration before saving it to S3

## Static Generation

The `model_config.proto` files, generated protobuf modules, and Triton release
mapping are checked into the Triton Control repository. Download and generation
happen statically for each Triton Control version, not dynamically at runtime.

This means a deployed Triton Control version supports the Triton release branches
included in that release.
