"""
Central API v1 router – aggregates all endpoint routers.
"""

from fastapi import APIRouter

from app.api.v1.endpoints.api_keys import router as api_keys_router
from app.api.v1.endpoints.audit import router as audit_router
from app.api.v1.endpoints.auth import router as auth_router
from app.api.v1.endpoints.network import router as network_router, port_forward_router
from app.api.v1.endpoints.projects import router as projects_router
from app.api.v1.endpoints.storage import image_router, pool_router
from app.api.v1.endpoints.system import router as system_router
from app.api.v1.endpoints.backups_admin import router as backups_admin_router
from app.api.v1.endpoints.tasks import router as tasks_router
from app.api.v1.endpoints.tenants import router as tenants_router
from app.api.v1.endpoints.tenant_networks import router as tenant_networks_router
from app.api.v1.endpoints.users import router as users_router
from app.api.v1.endpoints.notifications import router as notifications_router
from app.api.v1.endpoints.reverse_proxy import router as reverse_proxy_router
from app.api.v1.endpoints.vm_templates import router as vm_templates_router
from app.api.v1.endpoints.cluster import router as cluster_router
from app.api.v1.endpoints.load_balancer import router as load_balancer_router
from app.api.v1.endpoints.vms import router as vms_router

api_router = APIRouter()

api_router.include_router(auth_router)
api_router.include_router(api_keys_router)
api_router.include_router(tenants_router)
api_router.include_router(tenant_networks_router)
api_router.include_router(projects_router)
api_router.include_router(vms_router)
api_router.include_router(pool_router)
api_router.include_router(image_router)
api_router.include_router(network_router)
api_router.include_router(port_forward_router)
api_router.include_router(reverse_proxy_router)
api_router.include_router(load_balancer_router)
api_router.include_router(cluster_router)
api_router.include_router(tasks_router)
api_router.include_router(audit_router)
api_router.include_router(notifications_router)
api_router.include_router(users_router)
api_router.include_router(vm_templates_router)
api_router.include_router(system_router)
api_router.include_router(backups_admin_router)
