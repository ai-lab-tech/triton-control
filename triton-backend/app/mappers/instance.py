"""Mappers from ``TritonInstanceEntity`` to instance-related DTOs.

Provides two conversion functions:
  ``s3_entity_to_dto(entity)``  — extracts the S3 sub-configuration from a
    ``TritonInstanceEntity`` and returns an ``InstanceS3ConfigDTO``.
  ``entity_to_dto(entity)``     — converts a full ``TritonInstanceEntity``
    to a ``TritonInstanceDTO``, embedding the S3 DTO via ``s3_entity_to_dto``.
"""

from app.db.entities import TritonInstanceEntity
from app.schemas import InstanceS3ConfigDTO, TritonInstanceDTO


def s3_entity_to_dto(entity: TritonInstanceEntity) -> InstanceS3ConfigDTO:
    """Build an InstanceS3ConfigDTO from a Triton instance entity."""
    return InstanceS3ConfigDTO(
        enabled=bool(entity.s3_enabled),
        endpoint=entity.s3_endpoint,
        bucket=entity.s3_bucket,
        region=entity.s3_region,
        prefix=entity.s3_prefix,
        access_key=entity.s3_access_key,
        secret_configured=bool(entity.s3_secret_key_enc),
        use_https=entity.s3_use_https,
        verify_ssl=entity.s3_verify_ssl,
        ca_certificate=entity.s3_ca_certificate or "",
        address_style=entity.s3_address_style,
    )


def entity_to_dto(entity: TritonInstanceEntity) -> TritonInstanceDTO:
    """Convert a Triton instance entity to an outgoing DTO."""
    dto = TritonInstanceDTO.model_validate(entity, from_attributes=True)
    dto.s3 = s3_entity_to_dto(entity)
    return dto
