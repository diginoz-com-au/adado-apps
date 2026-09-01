#!/usr/bin/env python3
"""Validate AdaDo agent definitions (the `agent:` block in apps/*.yaml) against
the canonical spec in schema/agent.schema.json.

Stdlib-only where possible. Uses `jsonschema` if installed for full draft-07
validation; otherwise falls back to a built-in check of the spec's hard rules
(required fields, id pattern, enums). Exit code is non-zero if any agent fails.

Usage:
    python3 scripts/validate-agents.py                # all apps/*.yaml
    python3 scripts/validate-agents.py apps/projects.yaml
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "schema" / "agent.schema.json"

ID_RE = re.compile(r"^[a-z][a-z0-9-]*-agent$")
APP_ID_RE = re.compile(r"^adado-[a-z0-9-]+$")
TRIGGER_TYPES = {"message", "cron", "webhook", "event", "manual"}
CRED_TYPES = {"api_key", "bearer_token", "oauth", "basic", "session_cookie", "none"}


def load_yaml(path):
    try:
        import yaml  # PyYAML
    except ImportError:
        sys.exit("ERROR: PyYAML required. `pip install pyyaml`")
    with open(path) as fh:
        return yaml.safe_load(fh)


def builtin_validate(agent):
    """Fallback validator covering the spec's hard rules."""
    errs = []
    for field in ("id", "display_name", "description", "capabilities"):
        if field not in agent or agent[field] in (None, "", []):
            errs.append(f"missing required field: {field}")
    if "id" in agent and isinstance(agent["id"], str) and not ID_RE.match(agent["id"]):
        errs.append(f"id '{agent['id']}' does not match ^[a-z][a-z0-9-]*-agent$")
    caps = agent.get("capabilities")
    if caps is not None and (not isinstance(caps, list) or not caps):
        errs.append("capabilities must be a non-empty list")
    for app in agent.get("required_apps", []) or []:
        if not APP_ID_RE.match(str(app)):
            errs.append(f"required_apps entry '{app}' must match ^adado-[a-z0-9-]+$")
    for hook in agent.get("trigger_hooks", []) or []:
        if not isinstance(hook, dict) or "type" not in hook:
            errs.append(f"trigger_hook malformed (needs 'type'): {hook!r}")
        elif hook["type"] not in TRIGGER_TYPES:
            errs.append(f"trigger_hook type '{hook['type']}' not in {sorted(TRIGGER_TYPES)}")
    auth = agent.get("auth")
    if auth is not None:
        if "required" not in auth:
            errs.append("auth.required is required when auth is present")
        for cred in auth.get("credentials", []) or []:
            if cred.get("type") not in CRED_TYPES:
                errs.append(f"auth credential type '{cred.get('type')}' not in {sorted(CRED_TYPES)}")
    return errs


def schema_validate(agent, schema):
    try:
        import jsonschema
    except ImportError:
        return None  # signal fallback
    v = jsonschema.Draft7Validator(schema)
    return [f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in v.iter_errors(agent)]


def main():
    schema = json.loads(SCHEMA_PATH.read_text())
    targets = [Path(a) for a in sys.argv[1:]] or sorted((ROOT / "apps").glob("*.yaml"))
    total = ok = skipped = 0
    failures = []
    used_schema = None
    for path in targets:
        data = load_yaml(path)
        if not isinstance(data, dict) or "agent" not in data:
            skipped += 1
            continue
        total += 1
        agent = data["agent"]
        errs = schema_validate(agent, schema)
        if errs is None:
            used_schema = False
            errs = builtin_validate(agent)
        else:
            used_schema = True
        if errs:
            failures.append((path.name, errs))
        else:
            ok += 1
    mode = "jsonschema (draft-07)" if used_schema else "built-in fallback"
    print(f"Validated {total} agent block(s) using {mode}. {ok} passed, {len(failures)} failed, {skipped} file(s) had no agent.")
    for name, errs in failures:
        print(f"\n  ✗ {name}")
        for e in errs:
            print(f"      - {e}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
