#!/usr/bin/env python3
"""Run the isolated qualification fixture; failures never enable runtime builds."""

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlmodel import Session, create_engine

from app.db.schema import create_tables
from app.repositories import build_qualifications
from app.schemas.build_policy import BuildPolicy
from app.services.builds.provisioning import staging_profile
from app.services.builds.qualification import all_checks_passed
from scripts.builder_fixture import Fixture


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument(
        "--s3-config",
        type=Path,
        help="Private provisioning JSON: profile_name, endpoint, bucket, access_key, secret_key; never retained",
    )
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument(
        "--database-url", required=True, help="Qualification database; use application DATABASE_URL in installation"
    )
    args = parser.parse_args()
    kubeconfig = os.getenv("KUBECONFIG") or os.getenv("KUBERNETES_KUBECONFIG_PATH") or str(Path.home() / ".kube/config")
    if os.pathsep in kubeconfig:
        parser.error("Use one flattened kubeconfig file for qualification")
    os.environ["KUBECONFIG"] = kubeconfig
    os.environ["KUBERNETES_KUBECONFIG_PATH"] = kubeconfig
    policy = BuildPolicy.model_validate_json(args.policy.read_text())
    engine = create_engine(args.database_url)
    create_tables(engine)
    if args.s3_config:
        if args.s3_config.stat().st_mode & 0o077:
            parser.error("S3 provisioning file must be owner-only (chmod 600)")
        staging = json.loads(args.s3_config.read_text())
    else:
        with Session(engine) as session:
            staging = staging_profile(session, policy.s3_profile_name)
    if staging["profile_name"] != policy.s3_profile_name:
        parser.error("Staging credentials must match the policy S3 profile")
    if staging.get("profile_prefix") and not policy.staging_prefix.startswith(
        staging["profile_prefix"].rstrip("/") + "/"
    ):
        parser.error("Build staging must stay inside the existing S3 profile prefix")
    files = [
        Path(__file__),
        Path(__file__).with_name("builder_fixture.py"),
        Path(__file__).parents[1] / "app/services/builds/manifests.py",
    ]
    revision = hashlib.sha256(b"".join(file.read_bytes() for file in files)).hexdigest()
    fixture = Fixture(policy, staging)
    with tempfile.TemporaryDirectory(prefix="builder-ca-") as temporary:
        if staging.get("ca_certificate"):
            cert = Path(temporary) / "ca.pem"
            cert.write_text(staging["ca_certificate"])
            staging["ca_file"] = str(cert)
        report = fixture.run()
    report.update(
        {
            "policy_hash": policy.fingerprint(),
            "fixture_revision": revision,
            "executor_image": policy.executor_image,
            "platform": policy.platform,
        }
    )
    report["passed"] = all_checks_passed(report["checks"])
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(report, indent=2, sort_keys=True) + "\n"
    args.evidence.write_text(data)
    with Session(engine) as session:
        record = build_qualifications.append(
            session,
            policy_hash=policy.fingerprint(),
            fixture_revision=revision,
            executor_image=policy.executor_image,
            platform=policy.platform,
            passed=report["passed"],
            checks=report["checks"],
            evidence_path=str(args.evidence),
            evidence_sha256=hashlib.sha256(data.encode()).hexdigest(),
        )
        print(json.dumps({"record_id": record.id, "passed": report["passed"], "checks": report["checks"]}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
