#!/usr/bin/env python3
"""A–Z smoke tests for recent tenant/project/admin changes. Run inside API container."""

from __future__ import annotations

import asyncio
import json
import uuid

import httpx
from sqlalchemy import select

from app.core.database import async_session_factory
from app.core.security import create_access_token
from app.models.user import User

BASE = "http://127.0.0.1:8000/api/v1"
results: list[dict] = []


def ok(name: str, cond: bool, detail: str = "") -> None:
    results.append({"test": name, "pass": bool(cond), "detail": detail})


async def main() -> None:
    async with async_session_factory() as db:
        admin = (
            await db.execute(
                select(User).where(User.is_superadmin.is_(True), User.is_active.is_(True)).limit(1)
            )
        ).scalar_one_or_none()
        ok("superadmin exists", admin is not None, getattr(admin, "username", None) or "missing")
        if not admin:
            print(json.dumps({"summary": _summary(), "results": results}, indent=2))
            return

        token = create_access_token(str(admin.id))
        headers = {"Authorization": f"Bearer {token}"}

        users = (
            await db.execute(
                select(User).where(User.is_superadmin.is_(False), User.is_active.is_(True)).limit(5)
            )
        ).scalars().all()
        ok("non-superadmin users available", len(users) >= 1, f"count={len(users)}")
        target = users[0] if users else None

        async with httpx.AsyncClient(base_url=BASE, headers=headers, timeout=30.0) as client:
            r = await client.get("/tenants")
            ok(
                "GET /tenants",
                r.status_code == 200,
                f"status={r.status_code} n={len(r.json()) if r.status_code == 200 else r.text[:80]}",
            )
            tenants = r.json() if r.status_code == 200 else []

            for t in tenants[:5]:
                pr = await client.get(f"/tenants/{t['id']}/projects")
                n = len(pr.json()) if pr.status_code == 200 else 0
                ok(
                    f"projects for {t.get('slug', '?')}",
                    pr.status_code == 200 and n >= 1,
                    f"status={pr.status_code} n={n}",
                )

            slug = f"qa-a2z-{uuid.uuid4().hex[:8]}"
            create_body = {
                "name": f"QA A2Z {slug[-8:]}",
                "slug": slug,
                "description": "automated a2z test",
                "max_vms": 2,
                "max_vcpus": 4,
                "max_ram_mb": 4096,
                "max_storage_gb": 40,
                "max_ips": 0,
            }
            cr = await client.post("/tenants", json=create_body)
            ok(
                "POST /tenants (no admin)",
                cr.status_code == 201,
                f"status={cr.status_code} body={cr.text[:120]}",
            )
            if cr.status_code != 201:
                print(json.dumps({"summary": _summary(), "results": results}, indent=2))
                return
            tenant = cr.json()
            tid = tenant["id"]

            pr = await client.get(f"/tenants/{tid}/projects")
            names = [p["name"] for p in pr.json()] if pr.status_code == 200 else []
            ok("auto Default project", pr.status_code == 200 and "Default" in names, f"names={names}")

            mem = await client.get(f"/tenants/{tid}/users")
            ok("GET /tenants/{{id}}/users", mem.status_code == 200, f"status={mem.status_code}")
            members = mem.json() if mem.status_code == 200 else []
            roles = {m["user_id"]: m["role"] for m in members}
            ok(
                "creator is super_admin member",
                str(admin.id) in roles and roles[str(admin.id)] == "super_admin",
                f"roles={list(roles.values())}",
            )

            if target:
                up = await client.patch(
                    f"/tenants/{tid}",
                    json={"admin_user_id": str(target.id), "description": "qa admin assigned"},
                )
                ok(
                    "PATCH assign admin_user_id",
                    up.status_code == 200,
                    f"status={up.status_code} {up.text[:100]}",
                )
                mem2 = (await client.get(f"/tenants/{tid}/users")).json()
                admin_members = [
                    m
                    for m in mem2
                    if m["role"] == "tenant_admin" and m["user_id"] == str(target.id)
                ]
                ok("user became tenant_admin", len(admin_members) == 1, f"count={len(admin_members)}")

                up2 = await client.patch(f"/tenants/{tid}", json={"description": "qa no admin change"})
                ok(
                    "PATCH without admin_user_id keeps admin",
                    up2.status_code == 200,
                    f"status={up2.status_code}",
                )
                mem3 = (await client.get(f"/tenants/{tid}/users")).json()
                still = [
                    m
                    for m in mem3
                    if m["role"] == "tenant_admin" and m["user_id"] == str(target.id)
                ]
                ok("admin membership retained", len(still) == 1, f"still={len(still)}")

                # Change to another user if available
                other = next((u for u in users if u.id != target.id), None)
                if other:
                    up3 = await client.patch(
                        f"/tenants/{tid}",
                        json={"admin_user_id": str(other.id)},
                    )
                    ok(
                        "PATCH change tenant admin",
                        up3.status_code == 200,
                        f"status={up3.status_code}",
                    )
                    mem5 = (await client.get(f"/tenants/{tid}/users")).json()
                    has_other = any(
                        m["user_id"] == str(other.id) and m["role"] == "tenant_admin" for m in mem5
                    )
                    ok("new admin has tenant_admin role", has_other, f"ok={has_other}")

                slug2 = f"qa-adm-{uuid.uuid4().hex[:8]}"
                cr2 = await client.post(
                    "/tenants",
                    json={
                        "name": f"QA Admin {slug2[-8:]}",
                        "slug": slug2,
                        "max_vms": 1,
                        "max_vcpus": 2,
                        "max_ram_mb": 2048,
                        "max_storage_gb": 20,
                        "max_ips": 1,
                        "admin_user_id": str(target.id),
                    },
                )
                ok(
                    "POST /tenants with admin_user_id",
                    cr2.status_code == 201,
                    f"status={cr2.status_code} {cr2.text[:100]}",
                )
                if cr2.status_code == 201:
                    tid2 = cr2.json()["id"]
                    mem4 = (await client.get(f"/tenants/{tid2}/users")).json()
                    has_ta = any(
                        m["user_id"] == str(target.id) and m["role"] == "tenant_admin" for m in mem4
                    )
                    ok(
                        "create-time tenant_admin membership",
                        has_ta,
                        f"members={[ {'u': m['username'], 'r': m['role']} for m in mem4 ]}",
                    )
                    d2 = await client.delete(f"/tenants/{tid2}")
                    ok("DELETE cleanup tenant2", d2.status_code == 204, f"status={d2.status_code}")

            bad = await client.patch(f"/tenants/{tid}", json={"admin_user_id": str(uuid.uuid4())})
            ok("PATCH invalid admin -> 404", bad.status_code == 404, f"status={bad.status_code}")

            if tenants:
                t0 = tenants[0]["id"]
                projs = (await client.get(f"/tenants/{t0}/projects")).json()
                if projs:
                    pid = projs[0]["id"]
                    vl = await client.get(
                        f"/tenants/{t0}/vms", params={"project_id": pid, "page_size": 5}
                    )
                    total = vl.json().get("total") if vl.status_code == 200 else None
                    ok(
                        "GET /vms?project_id=",
                        vl.status_code == 200,
                        f"status={vl.status_code} total={total}",
                    )
                    # second project creation for multi-project UX backend support
                    p2 = await client.post(
                        f"/tenants/{t0}/projects",
                        json={"name": f"QA-Extra-{uuid.uuid4().hex[:6]}", "description": "a2z"},
                    )
                    # may 201 or 403 depending on role — superadmin should work
                    ok(
                        "POST second project (multi-project path)",
                        p2.status_code in (201, 200),
                        f"status={p2.status_code} {p2.text[:80]}",
                    )
                    if p2.status_code in (201, 200):
                        # soft deactivate via delete
                        await client.delete(f"/tenants/{t0}/projects/{p2.json()['id']}")

            me = await client.get("/auth/me")
            ok("GET /auth/me", me.status_code == 200, f"status={me.status_code}")
            me_body = me.json() if me.status_code == 200 else {}
            ok(
                "profile payload has identity fields",
                "email" in me_body and "username" in me_body,
                f"keys={sorted(me_body.keys())[:12]}",
            )

            # SSH keys list surface (may be empty)
            # endpoint may be nested — try common paths
            for path in ("/auth/me",):
                pass

            d1 = await client.delete(f"/tenants/{tid}")
            ok("DELETE cleanup tenant1", d1.status_code == 204, f"status={d1.status_code}")

            openapi = await httpx.AsyncClient(timeout=15.0).get(
                "http://127.0.0.1:8000/api/openapi.json",
                headers=headers,
            )
            schema_ok = False
            detail = f"status={openapi.status_code}"
            if openapi.status_code == 200:
                text = openapi.text
                schema_ok = "admin_user_id" in text
                detail = f"admin_user_id_in_schema={schema_ok}"
            ok("OpenAPI mentions admin_user_id", schema_ok, detail)

    print(json.dumps({"summary": _summary(), "results": results}, indent=2))


def _summary() -> dict:
    return {
        "pass": sum(1 for x in results if x["pass"]),
        "fail": sum(1 for x in results if not x["pass"]),
        "total": len(results),
    }


if __name__ == "__main__":
    asyncio.run(main())
