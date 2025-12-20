import yaml


def _clean_lines(raw_lines):
    cleaned = []
    if not raw_lines:
        return cleaned
    for line in raw_lines:
        if line is None:
            continue
        text = str(line).strip()
        if text:
            cleaned.append(text)
    return cleaned


def generate_recipe_yaml(template_model: dict) -> str:
    model = template_model or {}
    recipe = {"schema_version": 1}

    display_name = (model.get("display_name") or "").strip()
    if display_name:
        recipe["display_name"] = display_name

    workdir = (model.get("workdir") or "").strip()
    if workdir:
        recipe["workdir"] = workdir

    for block_name in ("clone_block", "init_block", "build_block"):
        block = model.get(block_name) or {}
        lines = _clean_lines(block.get("lines") if isinstance(block, dict) else None)
        if lines:
            recipe[block_name] = {"lines": lines}

    return yaml.safe_dump(recipe, sort_keys=False, default_flow_style=False)
