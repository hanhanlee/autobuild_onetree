import argparse
import json
import os
import sys
from pathlib import Path


def log(msg: str) -> None:
    print(msg, flush=True)


def is_safe_path(base: Path, target: Path) -> bool:
    try:
        return base in target.resolve().parents or target.resolve() == base
    except Exception:
        return False


def apply_patches(patches_path: Path, workdir: str | None = None) -> int:
    try:
        patches = json.loads(patches_path.read_text(encoding="utf-8"))
    except Exception as exc:
        log(f"[Error] Failed to read patches file: {exc}")
        return 1

    if not isinstance(patches, list):
        log("[Error] patches.json must contain a list")
        return 1

    base = Path(os.getcwd()).resolve()
    wd = Path(workdir).resolve() if workdir else None
    log(f"[Patch] Base directory: {base}")
    if wd:
        log(f"[Patch] Workdir hint: {wd}")

    def resolve_target(path_val: str) -> Path | None:
        candidate = (base / path_val).resolve()
        if candidate.exists():
            return candidate
        if wd:
            wd_candidate = (wd / path_val).resolve()
            if wd_candidate.exists():
                return wd_candidate
        # Fallback: search by filename under base
        name = Path(path_val).name
        matches = list(base.rglob(name))
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            log(f"[Warn] Ambiguous matches for {path_val}: {matches}")
            return None
        log(f"[Warn] Target not found for {path_val}")
        return None

    for item in patches:
        try:
            path_val = (item.get("path") or "").strip()
            action = (item.get("action") or "").strip().lower()
            content = item.get("content") or ""
            find_text = item.get("find") or ""

            if not path_val or ".." in Path(path_val).parts or path_val.startswith("/"):
                log(f"[Error] Unsafe path skipped: {path_val}")
                continue

            target = resolve_target(path_val)
            if target is None:
                continue
            if not is_safe_path(base, target):
                log(f"[Error] Unsafe target skipped: {path_val}")
                continue

            target.parent.mkdir(parents=True, exist_ok=True)

            if action == "append":
                with open(target, "a", encoding="utf-8") as f:
                    f.write(f"\n{content}\n")
                log(f"[Patch] Appended to {path_val}")

            elif action == "replace":
                if not target.exists():
                    log(f"[Warn] Replace target missing: {path_val}")
                    continue
                raw = target.read_text(encoding="utf-8")
                if find_text in raw:
                    target.write_text(raw.replace(find_text, content), encoding="utf-8")
                    log(f"[Patch] Replaced content in {path_val}")
                else:
                    log(f"[Warn] Find text not found in {path_val}")
            else:
                log(f"[Warn] Unknown action '{action}' for {path_val}")
        except Exception as exc:
            log(f"[Error] Patching {item}: {exc}")
            continue
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply file patches")
    parser.add_argument("patches", help="Path to patches.json")
    parser.add_argument("--workdir", help="Optional workdir hint", default=None)
    args = parser.parse_args()

    patches_path = Path(args.patches)
    if not patches_path.exists():
        print(f"patches file not found: {patches_path}", file=sys.stderr)
        return 1
    return apply_patches(patches_path, workdir=args.workdir)


if __name__ == "__main__":
    raise SystemExit(main())
