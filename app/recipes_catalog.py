import re
import yaml  # noqa: F401 - used for parsing recipe files
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import get_presets_root


def recipe_path_from_id(presets_root: Path, recipe_id: str) -> Path:
    if not re.match(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$", recipe_id or ""):
        raise ValueError("Invalid recipe id")
    parts = recipe_id.split("/", 1)
    return presets_root / parts[0] / f"{parts[1]}.yaml"


def _guess_display_name(path: Path) -> Optional[str]:
    try:
        for line in path.open("r", encoding="utf-8"):
            line = line.strip()
            if line.startswith("display_name:"):
                value = line.split("display_name:", 1)[1].strip().strip('"').strip("'")
                return value or None
    except Exception:
        return None
    return None


def _iter_recipe_files(presets_root: Path):
    exts = {".yaml", ".yml"}
    for path in presets_root.rglob("*"):
        if path.is_file() and path.suffix.lower() in exts:
            yield path


def list_recipes(presets_root: Optional[Path] = None) -> List[Dict[str, Any]]:
    presets_root = presets_root or get_presets_root()
    recipes: List[Dict[str, Any]] = []
    if not presets_root.exists():
        return recipes

    for path in _iter_recipe_files(presets_root):
        rel = path.relative_to(presets_root)
        if len(rel.parts) != 2:
            continue
        platform = rel.parts[0]
        project = path.stem
        recipe_id = f"{platform}/{project}"

        recipe_data: Dict[str, Any] = {
            "id": recipe_id,
            "display_name": recipe_id,
        }

        try:
            content = path.read_text(encoding="utf-8")
            data = yaml.safe_load(content)
            if isinstance(data, dict):
                recipe_data.update(data)
        except Exception:
            pass

        if "display_name" not in recipe_data or not recipe_data["display_name"]:
            fallback = _guess_display_name(path)
            recipe_data["display_name"] = fallback or recipe_id

        recipes.append(recipe_data)

    recipes.sort(key=lambda x: x["id"])
    return recipes


def load_recipe_yaml(presets_root: Path, recipe_id: str) -> str:
    path = recipe_path_from_id(presets_root, recipe_id)
    if not path.exists():
        raise FileNotFoundError(f"Recipe not found: {recipe_id}")
    return path.read_text(encoding="utf-8")
