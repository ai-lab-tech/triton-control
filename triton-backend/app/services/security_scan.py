"""Run the v1 serving-artifact checks in a trusted development/build environment."""

import argparse
import hashlib
import json
import os
import subprocess  # nosec B404
import sys
from collections.abc import Iterator
from datetime import datetime, timezone
from importlib.metadata import version as installed_version
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.core.canonical import canonical_bytes, digest, inventory_digest
from app.schemas.gate_evidence import SecurityReport, SignedSecurityReport
from app.schemas.serving_metadata import ServingRelease
from app.services.candidates.artifacts import CaptureLimits
from app.services.gate_evidence import security_message
from app.services.materialization.budgets import configured_limits
from app.services.materialization.cli import read_document
from app.services.materialization.filesystem import inventory


def inspect_scan_input(path: str, chunks: Iterator[bytes]) -> None:
    # Trivy discards inline-suppressed results before formatting its report.
    # Helm can generate such directives from templates, so scan rendered YAML.
    name = path.rsplit("/", 1)[-1]
    if name == "Chart.yaml" or name.endswith((".tgz", ".tar.gz")):
        raise ValueError("Scan rendered Helm manifests instead of charts or chart archives")

    scanner_prefix = ignore_section = False
    tail = b""
    for chunk in chunks:
        data = tail + chunk
        scanner_prefix |= b"trivy:" in data or b"tfsec:" in data
        ignore_section |= b"ignore:" in data
        if scanner_prefix and ignore_section:
            raise ValueError("Scan input contains a scanner suppression directive")
        tail = data[-6:]


def run_trivy(arguments: list[str], destination: Path) -> dict[str, Any]:
    with destination.open("xb") as output:
        subprocess.run(["trivy", "--config=", *arguments], stdout=output, check=True, timeout=1800)  # nosec B603 B607
    with destination.open("rb") as stream:
        result = json.load(stream)
    if not isinstance(result, dict):
        raise ValueError("Invalid Trivy report")
    return result


def scan(
    release: ServingRelease,
    source: Path,
    models: Path,
    destination: Path,
    issuer: str,
    key: Path,
    *,
    limits: CaptureLimits = CaptureLimits(),
) -> None:
    if os.getenv("TRITON_CONTROL_MODE") != "development":
        raise ValueError("Security scanning requires development mode")
    subjects = release.spec.validation.subjects
    prepared = inventory(models, limits, inspect_file=inspect_scan_input)
    if not prepared.files:
        raise ValueError("Prepared repository inventory is empty")
    if (
        inventory_digest(prepared) != subjects.prepared_inventory_digest
        or inventory_digest(inventory(source, limits, inspect_file=inspect_scan_input)) != subjects.inventory_digest
    ):
        raise ValueError("Mounted artifact inventories differ from the release")
    destination.mkdir(mode=0o700, parents=False, exist_ok=False)
    version = run_trivy(["version", "--format", "json"], destination / "version.json")["Version"]
    code_reports = []
    roots = [source] if subjects.inventory_digest == subjects.prepared_inventory_digest else [source, models]
    bandit_config = destination / "bandit.ini"
    bandit_config.write_text("[bandit]\n")
    python_reports = []
    for index, root in enumerate(roots):
        with (destination / f"python-{index}.json").open("xb") as output:
            subprocess.run(  # nosec B603
                [
                    sys.executable,
                    "-I",
                    "-m",
                    "bandit",
                    "--ini",
                    str(bandit_config.resolve()),
                    "--ignore-nosec",
                    "--exit-zero",
                    "-f",
                    "json",
                    "-r",
                    str(root.resolve()),
                ],
                check=True,
                stdout=output,
                timeout=1800,
            )
        python_report = json.loads((destination / f"python-{index}.json").read_bytes())
        if python_report["errors"]:
            raise ValueError("Python code scan incomplete")
        python_reports.append(python_report)
        code_reports.append(
            run_trivy(
                [
                    "fs", "--format", "json", "--ignorefile=", "--secret-config=",
                    "--scanners", "misconfig,secret", str(root.resolve()),
                ],
                destination / f"code-{index}.json",
            )
        )
    image_reports = []
    images = [release.spec.image]
    if release.spec.repository.materializer_image:
        images.append(release.spec.repository.materializer_image)
    for index, image in enumerate(images):
        # A platform-specific scan cannot attest every child of a manifest list.
        raw = subprocess.check_output(  # nosec B603 B607
            ["docker", "buildx", "imagetools", "inspect", "--raw", image],
            timeout=120,
        )
        if "sha256:" + hashlib.sha256(raw).hexdigest() != image.split("@")[1] or "layers" not in json.loads(raw):
            raise ValueError("Expected a single-platform image manifest at its exact digest")
        result = run_trivy(
            [
                "image", "--image-src", "remote", "--format", "json", "--ignorefile=",
                "--scanners", "vuln", "--list-all-pkgs", image,
            ],
            destination / f"image-{index}.json",
        )
        if not any(row.get("Packages") for row in result.get("Results", [])):
            raise ValueError("Missing image dependency inventory")
        image_reports.append(result)
    if any(item["SchemaVersion"] != 2 for item in code_reports + image_reports):
        raise ValueError("Unsupported Trivy report schema")
    code_count = sum(
        len(row.get("Secrets", [])) + sum(f["Status"] == "FAIL" for f in row.get("Misconfigurations", []))
        for item in code_reports
        for row in item.get("Results", [])
    )
    rows = [row for item in image_reports for row in item["Results"]]
    report = SecurityReport.model_validate(
        dict(
            suite="triton-security-v2",
            issuer=issuer,
            subjects=subjects,
            issuedAt=datetime.now(timezone.utc),
            trivyVersion=version,
            banditVersion=installed_version("bandit"),
            codeFindings=code_count + sum(len(item["results"]) for item in python_reports),
            imageFindings=sum(
                v["Severity"] in {"HIGH", "CRITICAL"} for row in rows for v in row.get("Vulnerabilities", [])
            ),
            packages=sum(len(row.get("Packages", [])) for row in rows),
            codeReport=digest([code_reports, python_reports]),
            imageReport=digest(image_reports),
            modelFiles=len(prepared.files),
        )
    )
    private = Ed25519PrivateKey.from_private_bytes(key.read_bytes())
    signed = SignedSecurityReport(report=report, signature=private.sign(security_message(report)).hex())
    (destination / "model-inventory.json").write_bytes(canonical_bytes(prepared.model_dump(mode="json")))
    (destination / "security.json").write_bytes(canonical_bytes(signed.model_dump(mode="json", by_alias=True)))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("release", "source", "models", "destination", "key"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--limits", help="Materialization capture budget JSON file")
    parser.add_argument("--issuer", required=True)
    args = parser.parse_args()
    scan(
        ServingRelease.model_validate_json(args.release.read_bytes()),
        args.source,
        args.models,
        args.destination,
        args.issuer,
        args.key,
        limits=configured_limits(read_document(args.limits)) if args.limits else CaptureLimits(),
    )


if __name__ == "__main__":
    main()
