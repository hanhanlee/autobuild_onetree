# Recipe YAML schema v1

Authoring guidance for the current runner (schema_version: 1). This describes the fields the runner reads today and how they execute; it does not propose new executable features.

## Location and identity
- Files live at `<presets_root>/<platform>/<project>.yaml`.
- The filesystem path defines `recipe_id` as `<platform>/<project>`; YAML `id` is optional, but if present it must match the path-derived `recipe_id`.
- `display_name` is optional and used only for UI display; the runner falls back to `recipe_id` when absent.

## Supported fields (v1)
- `schema_version` (required): must be `1`.
- `id` (optional): when provided, must equal `<platform>/<project>` derived from the file path.
- `display_name` (optional): human-friendly label; not used by the runner logic.
- `workdir` (optional): directory to `cd` into before running any blocks.
- `clone_block.lines` (optional list of strings): git clone and related setup commands. If omitted, clone is skipped.
- `init_block.lines` (optional list of strings): environment/setup commands that run after clone.
- `build_block.lines` (optional list of strings): build commands that run after init.
- Fields such as `file_appends`, `artifacts`, or other additions are not executed by the current runner.

## Execution order
1) Start script with `set -euo pipefail`.
2) `cd <workdir>` if `workdir` is set.
3) Run `clone_block.lines` in order (skipped when empty).
4) Run `init_block.lines` in order (skipped when empty).
5) Run `build_block.lines` in order (skipped when empty).

## Best practices
- Keep commands idempotent and re-runnable; avoid relying on shell state outside the recipe.
- Use explicit paths when cloning or configuring to avoid ambiguity with `workdir`.
- Edit `conf/local.conf` (or other config tweaks) inside `init_block.lines` because file append helpers are not supported in v1.
- Keep `clone_block` focused on fetching sources; defer environment setup (e.g., sourcing env scripts, exporting variables) to `init_block`.
- Keep `build_block` limited to build/invocation commands; avoid mutating workspace state that belongs in init.

## Minimal examples
```yaml
schema_version: 1
id: "platform/project"            # optional; must match path if present
display_name: "Platform Project"  # optional
workdir: "onetree"
clone_block:
  lines:
    - git clone https://example.com/onetree onetree
init_block:
  lines:
    - ./scripts/setup-env.sh
    - . env-setup
    - "# edit conf/local.conf here if needed"
build_block:
  lines:
    - bitbake obmc-phosphor-image
```
