import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, Request

from ..web import render_page

router = APIRouter()
logger = logging.getLogger(__name__)
WORKSPACES_ROOT = Path("/srv/autobuild/workspaces")


def _current_user(request: Request) -> Optional[str]:
    return request.session.get("user")


def _require_login(request: Request):
    if not _current_user(request):
        return render_page(
            request,
            "login.html",
            current_page="",
            error=None,
            status_code=200,
        )
    return None


def _list_codebases() -> Tuple[List[Dict[str, Optional[str]]], Dict[str, object]]:
    debug: Dict[str, object] = {
        "workspaces_root": str(WORKSPACES_ROOT),
        "codebases_count": 0,
        "last_error": None,
    }
    codebases: List[Dict[str, Optional[str]]] = []
    if not WORKSPACES_ROOT.exists():
        if not debug["last_error"]:
            debug["last_error"] = f"WORKSPACES_ROOT not accessible: {WORKSPACES_ROOT} (missing)"
        return codebases, debug
    if not WORKSPACES_ROOT.is_dir():
        if not debug["last_error"]:
            debug["last_error"] = f"WORKSPACES_ROOT not accessible: {WORKSPACES_ROOT} (not a directory)"
        return codebases, debug
    try:
        for child in WORKSPACES_ROOT.iterdir():
            if not child.is_dir():
                continue
            meta_path = child / "codebase.json"
            if not meta_path.exists():
                continue
            item = {
                "id": child.name,
                "label": child.name,
                "owner": None,
                "created_at": None,
                "last_used_at": None,
                "path": str(child),
            }
            try:
                data = json.loads(meta_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    item["label"] = data.get("label") or item["label"]
                    item["owner"] = data.get("owner")
                    item["created_at"] = data.get("created_at")
                    item["last_used_at"] = data.get("last_used_at")
            except Exception as exc:
                item["label"] = f"{item['id']} (invalid metadata)"
                if not debug["last_error"]:
                    debug["last_error"] = f"Failed to parse {meta_path}: {exc}"
                logger.warning("Failed to parse codebase metadata at %s: %s", meta_path, exc)
            codebases.append(item)
    except PermissionError as exc:
        debug["last_error"] = debug["last_error"] or f"Permission denied listing {WORKSPACES_ROOT}: {exc}"
    except Exception as exc:
        debug["last_error"] = debug["last_error"] or f"Failed to list codebases: {exc}"
        logger.warning("Failed to list codebases under %s: %s", WORKSPACES_ROOT, exc)
    codebases.sort(key=lambda c: c.get("id") or "")
    debug["codebases_count"] = len(codebases)
    return codebases, debug


@router.get("/codebases")
async def list_codebases(request: Request):
    auth_resp = _require_login(request)
    if auth_resp:
        return auth_resp
    user = _current_user(request)
    codebases, debug_ctx = _list_codebases()
    error_msg = debug_ctx.get("last_error")
    debug_json = json.dumps(debug_ctx, indent=2, ensure_ascii=False)
    return render_page(
        request,
        "codebases.html",
        current_page="codebases",
        codebases=codebases,
        debug_context=debug_ctx,
        debug_json=debug_json,
        codebases_count=debug_ctx.get("codebases_count", len(codebases)),
        workspaces_root=debug_ctx.get("workspaces_root"),
        error=error_msg,
        status_code=200,
        user=user,
        token_ok=None,
    )
