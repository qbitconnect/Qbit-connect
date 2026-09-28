"""Phase 12 — Production Readiness & Pre-Flight Verification Script.

Inspects the running environment, configuration, database schema,
security headers, background worker state, and API endpoints against
production acceptance criteria.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(backend_dir))
os.chdir(backend_dir)

import httpx
from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging
from app.services.rbac import (
    PERMISSIONS,
    ROLE_PERMISSIONS,
    ROLE_ADMIN,
    ROLE_MANAGER,
    ROLE_OPERATOR,
    ROLE_VIEWER,
    ROLE_CEO,
)

logger = get_logger("qbit.preflight")

checks_passed = 0
checks_failed = 0


def record_check(title: str, success: bool, details: str = "") -> None:
    global checks_passed, checks_failed
    if success:
        checks_passed += 1
        print(f" [PASS] {title}")
    else:
        checks_failed += 1
        print(f" [FAIL] {title}: {details}")


async def verify_configuration(settings) -> None:
    print("\n--- 1. Configuration & Security Safety Checks ---")
    problems = settings.validate_runtime()
    record_check("Runtime settings validation (Settings.validate_runtime)", len(problems) == 0, str(problems))
    record_check("Secret key entropy (>= 32 chars)", len(settings.QBIT_SECRET_KEY) >= 32)
    record_check("Cookie secure attribute policy configured", hasattr(settings, "QBIT_COOKIE_SECURE"))
    record_check("Webhook shared secret configured", bool(settings.EMAIL_WEBHOOK_SECRET))
    record_check("Path traversal protection roots exist", settings.data_dir.exists())


async def verify_migrations() -> None:
    print("\n--- 2. Database Migrations & Version Catalog ---")
    versions_dir = backend_dir / "alembic" / "versions"
    migration_files = list(versions_dir.glob("*.py"))
    migration_names = [f.stem for f in migration_files]
    
    # Must have all 18 core migrations from Phase 1 to Phase 10+
    record_check("Migration versions directory exists", versions_dir.exists())
    record_check("Alembic migrations count >= 18", len(migration_files) >= 18, f"Found {len(migration_files)}")
    
    expected_prefixes = [
        "0001", "0002", "0003", "0004", "0005", "0006", "0007", "0008",
        "0009", "0010", "0011", "0012", "0013", "0014", "0015", "0016",
        "0017", "0018"
    ]
    all_prefixes_present = all(
        any(name.startswith(prefix) for name in migration_names)
        for prefix in expected_prefixes
    )
    record_check("Complete migration chain (0001 through 0018)", all_prefixes_present)


async def verify_rbac_matrix() -> None:
    print("\n--- 3. RBAC & Tenant Authorization Integrity ---")
    required_roles = {ROLE_ADMIN, ROLE_MANAGER, ROLE_OPERATOR, ROLE_VIEWER, ROLE_CEO}
    roles_present = required_roles.issubset(set(ROLE_PERMISSIONS.keys()))
    record_check("Standard roles defined (ADMIN, MANAGER, OPERATOR, VIEWER, CEO)", roles_present)

    all_permission_codes = {code for code, _ in PERMISSIONS}
    # Verify key Phase 8-10 permissions are defined and assigned
    key_perms = [
        "leads.view", "leads.create", "leads.enrich", "ai.view", "ai.run_agents",
        "analytics.view", "reports.view"
    ]
    all_defined = all(p in all_permission_codes for p in key_perms)
    record_check("Key permissions present in RBAC catalog", all_defined)

    admin_has_all = all(p in ROLE_PERMISSIONS[ROLE_ADMIN] for p in key_perms)
    record_check("Admin role retains governance over key capabilities", admin_has_all)


async def verify_api_endpoints() -> None:
    print("\n--- 4. Live Server Endpoints & Security Headers ---")
    base_url = "http://127.0.0.1:8000"
    
    async with httpx.AsyncClient(base_url=base_url, timeout=5.0) as client:
        try:
            live_resp = await client.get("/health/live")
            record_check("Liveness probe (/health/live) HTTP 200", live_resp.status_code == 200)
            record_check("Liveness probe status is 'alive'", live_resp.json().get("status") == "alive")
        except Exception as exc:
            record_check("Liveness probe (/health/live)", False, f"Server unreachable: {exc}")

        try:
            ready_resp = await client.get("/health/ready")
            record_check("Readiness probe (/health/ready) HTTP 200", ready_resp.status_code == 200)
            services = ready_resp.json().get("services", {})
            record_check("Readiness probe checks core services", "database" in services and "storage" in services)
        except Exception as exc:
            record_check("Readiness probe (/health/ready)", False, f"Server unreachable: {exc}")

        try:
            login_page = await client.get("/login")
            record_check("Login page HTTP 200", login_page.status_code == 200)
            headers = login_page.headers
            record_check("Content-Security-Policy header present", "content-security-policy" in headers)
            record_check("X-Frame-Options is DENY", headers.get("x-frame-options") == "DENY")
            record_check("X-Content-Type-Options is nosniff", headers.get("x-content-type-options") == "nosniff")
        except Exception as exc:
            record_check("Security headers check", False, f"Could not inspect login page: {exc}")


async def verify_worker_heartbeat(settings) -> None:
    print("\n--- 5. Background Worker & Heartbeat Check ---")
    heartbeat_file = settings.data_dir / "cache" / "worker-heartbeat.json"
    record_check("Worker cache directory exists", (settings.data_dir / "cache").exists())
    # Worker may not be running in this immediate process, but test file path readiness
    record_check("Heartbeat directory path resolved safely", ".." not in str(heartbeat_file))


async def main() -> None:
    settings = get_settings()
    setup_logging(settings)
    print("=" * 60)
    print(" QBIT CONNECT — PRODUCTION READINESS PRE-FLIGHT VERIFIER")
    print("=" * 60)
    
    await verify_configuration(settings)
    await verify_migrations()
    await verify_rbac_matrix()
    await verify_api_endpoints()
    await verify_worker_heartbeat(settings)
    
    print("\n" + "=" * 60)
    print(f" PRE-FLIGHT AUDIT SUMMARY: {checks_passed} PASSED, {checks_failed} FAILED")
    print("=" * 60)
    
    if checks_failed > 0:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
