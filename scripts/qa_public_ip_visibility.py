#!/usr/bin/env python3
"""Verify public-IP assignment + tenant network visibility (superadmin + tenant user)."""

from __future__ import annotations

import asyncio
import json
import uuid

import httpx
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.database import async_session_factory
from app.core.security import create_access_token
from app.models.user import User, TenantUser

BASE = "http://127.0.0.1:8000/api/v1"
results: list[dict] = []


def ok(name: str, cond: bool, detail: str = "") -> None:
    results.append({"test": name, "pass": bool(cond), "detail": detail[:280]})


async def main() -> None:
    async with async_session_factory() as db:
        admin = (
            await db.execute(
                select(User).where(User.is_superadmin.is_(True), User.is_active.is_(True)).limit(1)
            )
        ).scalar_one_or_none()
        ok("superadmin", admin is not None, getattr(admin, "username", ""))
        if not admin:
            print(json.dumps({"results": results}, indent=2))
            return

        # Prefer user "dev1" / tenant with slug containing dev
        users = (
            await db.execute(
                select(User)
                .options(selectinload(User.tenant_memberships))
                .where(User.is_superadmin.is_(False), User.is_active.is_(True))
            )
        ).scalars().all()
        tenant_user = next((u for u in users if u.username == "dev1"), users[0] if users else None)
        ok("tenant user", tenant_user is not None, getattr(tenant_user, "username", ""))

        admin_token = create_access_token(str(admin.id))
        user_token = create_access_token(str(tenant_user.id)) if tenant_user else None

        async with httpx.AsyncClient(timeout=45.0) as client:
            ah = {"Authorization": f"Bearer {admin_token}"}

            free = await client.get(f"{BASE}/ip-pools/public/available", headers=ah)
            ok("admin list free public IPs", free.status_code == 200, f"n={len(free.json()) if free.status_code==200 else free.text[:80]}")
            free_ips = free.json() if free.status_code == 200 else []

            # Find / create a QA tenant with 2 public IPs
            slug = f"qa-vis-{uuid.uuid4().hex[:6]}"
            pick = [x["id"] for x in free_ips[:2]]
            cr = await client.post(
                f"{BASE}/tenants",
                headers=ah,
                json={
                    "name": f"QA Vis {slug[-6:]}",
                    "slug": slug,
                    "max_vms": 3,
                    "max_vcpus": 4,
                    "max_ram_mb": 4096,
                    "max_storage_gb": 40,
                    "max_ips": len(pick),
                    "public_ip_allocation_ids": pick,
                    "admin_user_id": str(tenant_user.id) if tenant_user else None,
                },
            )
            ok("create tenant with 2 public IPs", cr.status_code == 201, f"status={cr.status_code} {cr.text[:120]}")
            if cr.status_code != 201:
                print(json.dumps({"summary": _sum(), "results": results}, indent=2))
                return
            tenant = cr.json()
            tid = tenant["id"]
            ok("max_ips synced", tenant.get("max_ips") == len(pick), f"max_ips={tenant.get('max_ips')} pick={len(pick)}")

            assigned = await client.get(f"{BASE}/tenants/{tid}/public-ips", headers=ah)
            aips = assigned.json() if assigned.status_code == 200 else []
            ok("admin sees assigned public IPs", assigned.status_code == 200 and len(aips) == len(pick), f"n={len(aips)}")

            nets = await client.get(f"{BASE}/tenants/{tid}/networks", headers=ah)
            nlist = nets.json() if nets.status_code == 200 else []
            owned = [n for n in nlist if n.get("tenant_id") == tid]
            shared_nat = [n for n in nlist if n.get("tenant_id") is None and n.get("pool_type") == "nat"]
            ext = [n for n in nlist if n.get("pool_type") == "external"]
            ok("tenant has default NAT", len(owned) >= 1 and any(n["pool_type"] == "nat" for n in owned), f"owned={len(owned)}")
            ok("shared NAT hidden from tenant view", len(shared_nat) == 0, f"shared_nat={len(shared_nat)}")
            ok(
                "external pool only if IPs assigned",
                (len(ext) >= 1) if pick else (len(ext) == 0),
                f"ext={len(ext)} avail={ext[0].get('available_ips') if ext else None}",
            )
            if ext and pick:
                ok("external available_ips == assigned free", ext[0].get("available_ips") == len(pick), f"avail={ext[0].get('available_ips')}")

            # Full NAT create with advanced CIDR
            net = await client.post(
                f"{BASE}/tenants/{tid}/networks",
                headers=ah,
                json={
                    "name": "custom-nat",
                    "pool_type": "nat",
                    "network": "10.77.0.0/24",
                    "gateway": "10.77.0.1",
                    "dns_primary": "8.8.8.8",
                    "internal_dhcp_enabled": True,
                },
            )
            ok("create NAT with admin-like CIDR", net.status_code == 201, f"status={net.status_code} {net.text[:100]}")

            internal = await client.post(
                f"{BASE}/tenants/{tid}/networks",
                headers=ah,
                json={"name": "lan-a", "pool_type": "internal"},
            )
            ok("create Internal auto-CIDR", internal.status_code == 201, f"status={internal.status_code}")

            # Tenant user visibility
            if user_token:
                uh = {"Authorization": f"Bearer {user_token}"}
                unets = await client.get(f"{BASE}/tenants/{tid}/networks", headers=uh)
                ulist = unets.json() if unets.status_code == 200 else []
                u_shared = [n for n in ulist if n.get("tenant_id") is None and n.get("pool_type") != "external"]
                u_ext = [n for n in ulist if n.get("pool_type") == "external"]
                u_owned = [n for n in ulist if n.get("tenant_id") == tid]
                ok("tenant user GET networks", unets.status_code == 200, f"status={unets.status_code}")
                ok("tenant user: no shared NAT/Internal", len(u_shared) == 0, f"shared={len(u_shared)}")
                ok("tenant user: sees own nets", len(u_owned) >= 2, f"owned={len(u_owned)}")
                ok("tenant user: sees public pool scoped", len(u_ext) == (1 if pick else 0), f"ext={len(u_ext)}")

                uips = await client.get(f"{BASE}/tenants/{tid}/public-ips", headers=uh)
                ok(
                    "tenant user sees only assigned public IPs",
                    uips.status_code == 200 and len(uips.json()) == len(pick),
                    f"n={len(uips.json()) if uips.status_code==200 else uips.text[:60]}",
                )

                # VM create pool list filter
                pools = await client.get(f"{BASE}/ip-pools", headers=uh, params={"tenant_id": tid})
                plist = pools.json() if pools.status_code == 200 else []
                p_shared_nat = [p for p in plist if p.get("tenant_id") is None and p.get("pool_type") == "nat"]
                ok("tenant user ip-pools: no shared NAT", len(p_shared_nat) == 0, f"shared_nat={len(p_shared_nat)} n={len(plist)}")

            # Also check existing "dev" tenant if present
            tenants = (await client.get(f"{BASE}/tenants", headers=ah)).json()
            dev = next((t for t in tenants if "dev" in t.get("slug", "")), None)
            if dev and user_token:
                uh = {"Authorization": f"Bearer {user_token}"}
                dnets = await client.get(f"{BASE}/tenants/{dev['id']}/networks", headers=uh)
                if dnets.status_code == 200:
                    dshared = [n for n in dnets.json() if n.get("tenant_id") is None and n.get("pool_type") != "external"]
                    ok("dev tenant: no shared private nets", len(dshared) == 0, f"shared={len(dshared)}")
                else:
                    ok("dev tenant networks", False, f"status={dnets.status_code}")

            # Cleanup QA tenant networks then tenant
            for n in (await client.get(f"{BASE}/tenants/{tid}/networks", headers=ah)).json():
                if n.get("tenant_id") == tid:
                    await client.delete(f"{BASE}/tenants/{tid}/networks/{n['id']}", headers=ah)
            # Unassign public IPs before delete
            await client.patch(
                f"{BASE}/tenants/{tid}",
                headers=ah,
                json={"public_ip_allocation_ids": []},
            )
            d = await client.delete(f"{BASE}/tenants/{tid}", headers=ah)
            ok("cleanup QA tenant", d.status_code in (200, 204), f"status={d.status_code}")

    print(json.dumps({"summary": _sum(), "results": results}, indent=2))


def _sum() -> dict:
    return {
        "pass": sum(1 for x in results if x["pass"]),
        "fail": sum(1 for x in results if not x["pass"]),
        "total": len(results),
    }


if __name__ == "__main__":
    asyncio.run(main())
