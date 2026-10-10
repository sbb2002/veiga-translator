"""Hardware profile loader (profiles/<name>.yaml).

Opt-in only: with LT_PROFILE unset, apply_profile() is a no-op and every
constant in backend.config keeps its original value.

    LT_PROFILE=<name>             apply profiles/<name>.yaml's `config:` overrides
    LT_PROFILE_OVERLAYS=a,b       then apply these yaml files on top, in order

`extends` lists are applied before the profile's own `config:` (cycles are
rejected). This module must NOT import backend.config: config.py calls
apply_profile(globals()) at its very end, so the namespace is passed in.

CLI (run from the repo root):
    python -m backend.profile list
    python -m backend.profile validate [name]
    python -m backend.profile show <name>
    python -m backend.profile launch-args llama|whisper <name>
"""

import json
import logging
import os
import sys
from pathlib import Path

import yaml

logger = logging.getLogger("live-translator.backend")

REPO_ROOT = Path(__file__).resolve().parent.parent
PROFILES_DIR = REPO_ROOT / "profiles"

ALLOWED_TOP_KEYS = {
    "name", "description", "vram_budget_gb", "extends",
    "config", "llama_server", "whisper_server",
}
LAUNCHER_SECTIONS = ("llama_server", "whisper_server")
# Only these keys may ever be overridden; ACTIVE_PROFILE is bookkeeping.
PROTECTED_KEYS = {"ACTIVE_PROFILE"}


def _load_yaml(name):
    path = PROFILES_DIR / f"{name}.yaml"
    if not path.is_file():
        raise ValueError(f"profile '{name}' not found: {path}")
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"profile '{name}': top level must be a mapping")
    unknown = set(data) - ALLOWED_TOP_KEYS
    if unknown:
        raise ValueError(f"profile '{name}': unknown top-level keys {sorted(unknown)}")
    if data.get("name", name) != name:
        raise ValueError(f"profile '{name}': name field '{data.get('name')}' must match filename")
    for section in ("config", *LAUNCHER_SECTIONS):
        if section in data and not isinstance(data[section], dict):
            raise ValueError(f"profile '{name}': '{section}' must be a mapping")
    extends = data.get("extends") or []
    if not isinstance(extends, list) or not all(isinstance(e, str) for e in extends):
        raise ValueError(f"profile '{name}': 'extends' must be a list of profile names")
    return data


def _resolve(name, stack=()):
    """Return (chain, merged) where chain is the application order of names
    (extends first, then name itself) and merged holds the combined config
    dict plus launcher sections (later entries win)."""
    if name in stack:
        cycle = " -> ".join(list(stack) + [name])
        raise ValueError(f"extends cycle detected: {cycle}")
    data = _load_yaml(name)
    chain = []
    merged = {"config": {}, "llama_server": {}, "whisper_server": {}}
    for parent in data.get("extends") or []:
        p_chain, p_merged = _resolve(parent, stack + (name,))
        for item in p_chain:
            if item not in chain:
                chain.append(item)
        _merge_into(merged, p_merged)
    chain.append(name)
    _merge_into(merged, {
        "config": data.get("config") or {},
        "llama_server": data.get("llama_server") or {},
        "whisper_server": data.get("whisper_server") or {},
    })
    return chain, merged


def _merge_into(target, source):
    for section, values in source.items():
        target[section].update(values)


def resolve_stack(name, overlays=()):
    """Resolve a profile plus overlay profiles. Returns (chain, merged)."""
    chain, merged = _resolve(name)
    for overlay in overlays:
        o_chain, o_merged = _resolve(overlay)
        for item in o_chain:
            if item not in chain:
                chain.append(item)
        _merge_into(merged, o_merged)
    return chain, merged


def _env_overlays():
    raw = os.environ.get("LT_PROFILE_OVERLAYS", "")
    return [s.strip() for s in raw.split(",") if s.strip()]


def _check_value(key, new, current):
    """Return the value to store for `key`, or raise ValueError on a type
    mismatch against the value currently in backend.config."""
    if isinstance(current, bool):
        if not isinstance(new, bool):
            raise ValueError(f"{key}: expected bool, got {type(new).__name__}")
        return new
    if isinstance(current, float):
        # int -> float is the one implicit conversion allowed.
        if isinstance(new, bool) or not isinstance(new, (int, float)):
            raise ValueError(f"{key}: expected float, got {type(new).__name__}")
        return float(new)
    if isinstance(current, int):
        if isinstance(new, bool) or not isinstance(new, int):
            raise ValueError(f"{key}: expected int, got {type(new).__name__}")
        return new
    if current is None:
        return new
    if not isinstance(new, type(current)):
        raise ValueError(f"{key}: expected {type(current).__name__}, got {type(new).__name__}")
    return new


def apply_overrides(namespace, overrides):
    """Validate every override against `namespace` first, then write them all.
    Nothing is written if any key or type is wrong."""
    checked = {}
    for key, new in overrides.items():
        if key not in namespace:
            raise ValueError(f"unknown config key '{key}' (typo?)")
        if key in PROTECTED_KEYS:
            raise ValueError(f"config key '{key}' cannot be overridden by a profile")
        checked[key] = _check_value(key, new, namespace[key])
    namespace.update(checked)
    return len(checked)


def apply_profile(namespace):
    """Called at the end of backend/config.py with globals()."""
    name = os.environ.get("LT_PROFILE", "").strip()
    namespace["ACTIVE_PROFILE"] = None
    if not name:
        return
    overlays = _env_overlays()
    _, merged = resolve_stack(name, overlays)
    count = apply_overrides(namespace, merged["config"])
    namespace["ACTIVE_PROFILE"] = name
    suffix = f" + overlays {overlays}" if overlays else ""
    logger.info("Profile '%s' applied (%d overrides)%s", name, count, suffix)


# ---------------------------------------------------------------- CLI

def _config_namespace():
    # Import with the profile env vars cleared so validation sees plain defaults.
    os.environ.pop("LT_PROFILE", None)
    os.environ.pop("LT_PROFILE_OVERLAYS", None)
    from backend import config
    return dict(vars(config))


def _all_profile_names():
    return sorted(p.stem for p in PROFILES_DIR.glob("*.yaml"))


def _cmd_list(_args):
    for name in _all_profile_names():
        desc = _load_yaml(name).get("description", "")
        print(f"{name}\t{desc}")
    return 0


def _cmd_validate(args):
    names = args or _all_profile_names()
    try:
        namespace = _config_namespace()
    except Exception as e:  # config itself failing to import is a real error
        print(f"ERROR config import: {e}")
        return 1
    failed = 0
    for name in names:
        try:
            _, merged = _resolve(name)
            apply_overrides(dict(namespace), merged["config"])
            print(f"OK {name}")
        except ValueError as e:
            failed += 1
            print(f"ERROR {name}: {e}")
    return 1 if failed else 0


def _cmd_show(args):
    if len(args) != 1:
        print("usage: show <name>")
        return 2
    chain, merged = resolve_stack(args[0], _env_overlays())
    print(json.dumps({"chain": chain, **merged}, ensure_ascii=False, indent=2))
    return 0


def _cmd_launch_args(args):
    if len(args) != 2 or args[0] not in ("llama", "whisper"):
        print("usage: launch-args llama|whisper <name>")
        return 2
    kind, name = args
    section = "llama_server" if kind == "llama" else "whisper_server"
    _, merged = resolve_stack(name, _env_overlays())
    spec = merged[section]
    missing = [k for k in ("exe", "model", "port") if k not in spec]
    if missing:
        print(f"ERROR profile '{name}' has no {section} {missing}", file=sys.stderr)
        return 1
    out = {
        "exe": spec["exe"],
        "model": spec["model"],
        "port": spec["port"],
        "args": list(spec.get("args") or []),
    }
    print(json.dumps(out))  # ASCII-only: consumed by PowerShell, keep stdout plain
    return 0


_USAGE = """usage: python -m backend.profile <command>
  list                            list profiles/*.yaml
  validate [name]                 validate all (or one) profile; exit 1 on error
  show <name>                     print resolved profile (incl. LT_PROFILE_OVERLAYS)
  launch-args llama|whisper <name> print launcher JSON {exe, model, port, args}
"""


def main(argv):
    if not argv:
        print(_USAGE)
        return 0
    cmd, args = argv[0], argv[1:]
    handlers = {
        "list": _cmd_list,
        "validate": _cmd_validate,
        "show": _cmd_show,
        "launch-args": _cmd_launch_args,
    }
    if cmd not in handlers:
        print(_USAGE)
        return 2
    try:
        return handlers[cmd](args)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    # Windows consoles default to cp949; profile descriptions may contain non-ASCII.
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv[1:]))
