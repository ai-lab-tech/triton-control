from datetime import datetime, timezone
from unittest import TestCase

from sqlmodel import Session, SQLModel, create_engine

from app.db.entities import TritonInstanceEntity
from app.mappers.instance import entity_to_dto
from app.repositories.instances import shared_gpu_ids
from app.services.triton.client import TritonService
from app.services.triton.resource_usage import apply_triton_sample, apply_workload_sample


class DeviceScopeTests(TestCase):
    def test_device_overlap_and_cpu_only_instance_redaction(self):
        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        SQLModel.metadata.create_all(engine)
        with Session(engine) as session:
            for name, allocated in [("gpu", True), ("cpu", False)]:
                row = TritonInstanceEntity(name=name, url=f"http://{name}", is_self_deployed=True,
                                           metrics_url=f"http://{name}/metrics")
                sample = TritonService._parse_prometheus_metrics(
                    'nv_gpu_utilization{gpu_uuid="GPU-1"} 0.25\nnv_gpu_memory_used_bytes{gpu_uuid="GPU-1"} 256'
                )
                sample["checked_at"] = datetime.now(timezone.utc)
                apply_triton_sample(row, sample)
                apply_workload_sample(row, {"gpu_allocated": allocated})
                session.add(row)
                session.flush()
                shared = shared_gpu_ids(session)
                if allocated:
                    self.assertEqual(shared, set())
                    gpu = row
                else:
                    self.assertEqual(shared, {"GPU-1"})
                    dto = entity_to_dto(row, shared)
                    self.assertIsNone(dto.resource_usage.gpu_percent)
                    self.assertIsNone(dto.resource_usage.gpu_memory_used_bytes)
                    self.assertNotIn("gpu_device_ids", dto.resource_usage.model_dump())
                    self.assertTrue(entity_to_dto(gpu, shared).resource_usage.gpu_shared)
                    row.metrics_url = None
                    session.flush()
                    self.assertEqual(shared_gpu_ids(session), set())
