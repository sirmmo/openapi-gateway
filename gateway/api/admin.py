from fastapi import APIRouter, Request, HTTPException
from gateway.registry.route_registry import registry
from gateway.discovery.manual_loader import reload_manual_config
from gateway.discovery.docker_watcher import rediscover
from gateway.auth.api_keys import load_api_keys, count as api_key_count
from gateway.settings import settings

router = APIRouter(prefix="/_gateway")


def _verify_admin(request: Request):
    if not settings.admin_secret:
        raise HTTPException(
            status_code=503,
            detail="Admin endpoints disabled: GATEWAY_ADMIN_SECRET not configured"
        )
    secret = request.headers.get("X-Gateway-Admin-Secret", "")
    if secret != settings.admin_secret:
        raise HTTPException(status_code=403, detail="Invalid admin secret")


@router.get("/status")
async def status(request: Request):
    _verify_admin(request)
    return {
        "namespace": settings.namespace,
        "services": registry.status(),
    }


@router.get("/routes")
async def routes(request: Request):
    _verify_admin(request)
    return registry.all_routes()


@router.post("/reload")
async def reload(request: Request):
    _verify_admin(request)
    await reload_manual_config()
    return {"ok": True}


@router.post("/rediscover")
async def rediscover_docker(request: Request):
    _verify_admin(request)
    count = await rediscover()
    return {"ok": True, "discovered": count}


@router.post("/api-keys/reload")
async def reload_api_keys(request: Request):
    _verify_admin(request)
    n = load_api_keys()
    return {"ok": True, "loaded": n}


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
async def admin_not_found(path: str):
    raise HTTPException(status_code=404, detail=f"/_gateway/{path} is not a valid admin endpoint")
