import json
import logging
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, Request, Response

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
            if child.name.startswith("."):
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


def _sanitize_codebase_id(raw: str) -> Optional[str]:
    if not raw:
        return None
    if ".." in raw or "/" in raw or "\\" in raw:
        return None
    if not re.match(r"^[A-Za-z0-9._-]+$", raw):
        return None
    return raw


def _timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _prg_redirect(url: str) -> Response:
    return Response(status_code=303, headers={"Location": url})


@router.get("/codebases")
async def list_codebases(request: Request):
    auth_resp = _require_login(request)
    if auth_resp:
        return auth_resp
    user = _current_user(request)
    confirm_action = request.query_params.get("confirm") or ""
    confirm_codebase = request.query_params.get("codebase_id") or ""
    action_ok = request.query_params.get("action_ok") or ""
    action_error = request.query_params.get("action_error") or ""
    codebases, debug_ctx = _list_codebases()
    error_msg = debug_ctx.get("last_error")
    debug_json = json.dumps(debug_ctx, indent=2, ensure_ascii=False)
    if confirm_action not in {"archive", "delete"}:
        confirm_action = ""
    valid_ids = {cb.get("id") for cb in codebases if cb.get("id")}
    if confirm_action and confirm_codebase:
        if confirm_codebase not in valid_ids:
            confirm_action = ""
            confirm_codebase = ""
            if not error_msg:
                error_msg = "Codebase no longer exists"
    if action_error:
        error_msg = action_error
    success_msg = None
    if action_ok:
        success_msg = f"Action succeeded for {confirm_codebase or request.query_params.get('codebase_id') or ''}"
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
        success=success_msg,
        confirm_action=confirm_action if confirm_action in {"archive", "delete"} else "",
        confirm_codebase=confirm_codebase,
        status_code=200,
        user=user,
        token_ok=None,
    )


@router.post("/codebases/action")
async def codebases_action(request: Request):
    auth_resp = _require_login(request)
    if auth_resp:
        return auth_resp
    user = _current_user(request)
    try:
        form = await request.form()
    except Exception:
        return _prg_redirect("/codebases?action_error=Invalid+form+submission")
    action = str(form.get("action") or "").strip().lower()
    codebase_id_raw = str(form.get("codebase_id") or "").strip()
    codebase_id = _sanitize_codebase_id(codebase_id_raw)
    if action not in {"archive", "delete"} or not codebase_id:
        return _prg_redirect("/codebases?action_error=Invalid+action+or+codebase_id")
    codebase_dir = WORKSPACES_ROOT / codebase_id
    meta_path = codebase_dir / "codebase.json"
    if not codebase_dir.exists() or not meta_path.exists():
        return _prg_redirect("/codebases?action_error=Codebase+not+found")
    owner = None
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            owner = (data.get("owner") or "").strip()
    except Exception as exc:
        logger.warning("Failed to parse codebase metadata for %s: %s", codebase_id, exc)
    if owner and owner != (user or ""):
        msg = f"Permission denied: owner={owner}, user={user or ''}"
        resp = render_page(
            request,
            "codebases.html",
            current_page="codebases",
            codebases=[],
            debug_context={"last_error": msg, "workspaces_root": str(WORKSPACES_ROOT), "codebases_count": 0},
            debug_json=json.dumps({"last_error": msg}, indent=2, ensure_ascii=False),
            error=msg,
            status_code=403,
            user=user,
            token_ok=None,
        )
        return resp
    ts = _timestamp()
    suffix = f"{codebase_id}--{ts}"
    dest_root = WORKSPACES_ROOT / (".archived" if action == "archive" else ".trash")
    dest_root.mkdir(parents=True, exist_ok=True)
    dest_dir = dest_root / suffix
    try:
        try:
            os.replace(codebase_dir, dest_dir)
        except OSError:
            shutil.move(str(codebase_dir), str(dest_dir))
    except Exception as exc:
        logger.warning("Failed to move codebase %s to %s: %s", codebase_dir, dest_dir, exc)
        return _prg_redirect(f"/codebases?action_error=Failed+to+{action}+codebase")
    return _prg_redirect(f"/codebases?action_ok=1&action={action}&codebase_id={codebase_id}")
