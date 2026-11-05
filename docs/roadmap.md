# Product Roadmap

Planned enhancements and delivered capabilities for Triton Control.

## Focus

- Better scaling and operations for Triton deployments
- Higher productivity in the workspace
- Clearer administration and security options

## Planned Enhancements

### 1) Deployment, Operations, and Performance

#### Resource Management for Triton Instances

Goal:

- Provide clear management of GPU, RAM, and CPU resources for self-deployed Triton instances during deployment.
- Enable explicit configuration of requests and limits per self-deployed Triton instance.

#### Triton Deployment Scaling

Goal:

- Support changing the replica count of self-deployed Triton deployments.

### 2) Platform and Workspace

#### OpenAI API Support for Triton Server

Goal:

- Provide OpenAI API support as an integration option for Triton server workflows.

#### User Management with Groups

Goal:

- Extend user management with groups.
- Establish a foundation for role- and team-based administration.

## Prioritization

### Next

- Triton deployment scaling
- OpenAI API integration for Triton Server

### Later

- Resource management for Triton deployments (GPU/RAM/CPU)
- User management with groups

## Delivered

- Security and networking: administrator-managed Traefik routing, TLS through a cert-manager ClusterIssuer or existing TLS Secret, and HTTP Basic Authentication for model endpoints.

- [Per-model performance Jobs](model-performance-jobs.md): independent non-root Jobs, model-scoped status, and cancellation.
