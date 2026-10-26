"""Chapter 24: synthetic trusted deployment and observation fixtures.

All names, image digests, metrics and amounts are illustrative. No Kubernetes
API, image registry, user data, real external provider or live traffic.
"""
from __future__ import annotations

from decimal import Decimal
from .platform_deployment import (
    CanaryMetrics, PlatformManifest, ReleaseSpec, ServiceProfile, TenantProfile,
)


def sample_platform() -> PlatformManifest:
    image = "sha256:" + "a" * 64
    control = (
        ServiceProfile(
            name="agent-api", namespace="agent-control",
            service_account="agent-api-sa", image_digest=image,
            replicas=2, cpu_request_millis=250, cpu_limit_millis=1000,
            memory_request_mib=256, memory_limit_mib=1024,
            isolation="hardened-oci",
        ),
        ServiceProfile(
            name="agent-scheduler", namespace="agent-control",
            service_account="agent-scheduler-sa", image_digest=image,
            replicas=2, cpu_request_millis=500, cpu_limit_millis=1000,
            memory_request_mib=256, memory_limit_mib=1024,
            isolation="hardened-oci",
        ),
        ServiceProfile(
            name="tool-gateway", namespace="agent-control",
            service_account="tool-gateway-sa", image_digest=image,
            replicas=2, cpu_request_millis=500, cpu_limit_millis=1000,
            memory_request_mib=512, memory_limit_mib=1024,
            isolation="hardened-oci",
        ),
    )
    execution = ServiceProfile(
        name="sandbox-worker", namespace="agent-sandbox",
        service_account="sandbox-worker-sa", image_digest=image,
        replicas=2, cpu_request_millis=1000, cpu_limit_millis=2000,
        memory_request_mib=1024, memory_limit_mib=2048,
        isolation="gvisor",
    )
    tenants = (
        TenantProfile("tenant-a", "tenant-a", 100, 5, 6000, 8192),
        TenantProfile("tenant-b", "tenant-b", 50, 3, 4000, 4096),
    )
    return PlatformManifest(
        platform_id="demo-agent-platform", policy_version="v1",
        services=control + (execution,), tenants=tenants,
        stable_db_schema=2, candidate_min_db_schema=1,
        candidate_max_db_schema=3,
    )


def sample_release() -> ReleaseSpec:
    return ReleaseSpec(
        "release-001", "stable-v1", "candidate-v2", sample_platform()
    )


def healthy_metrics() -> tuple[CanaryMetrics, CanaryMetrics]:
    return (
        CanaryMetrics(
            elapsed_seconds=120, attempted=100, verified=99,
            p95_latency_ms=200, total_cost_usd=Decimal("10"),
            security_incidents=0,
        ),
        CanaryMetrics(
            elapsed_seconds=120, attempted=50, verified=49,
            p95_latency_ms=215, total_cost_usd=Decimal("5.10"),
            security_incidents=0,
        ),
    )
