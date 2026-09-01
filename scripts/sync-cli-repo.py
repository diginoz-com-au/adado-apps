#!/usr/bin/env python3
"""Sync local adado files → diginoz-com-au/adado-cli repo via GitHub API."""
import base64, json, os, subprocess, sys, time
import urllib.request, urllib.error

TOKEN = os.environ.get("GITHUB_TOKEN") or open(os.path.expanduser("~/.adado-github-token")).read().strip()
REPO  = "diginoz-com-au/adado-cli"
BASE  = "/home/ada/adado"
DIRS  = ["agents", "apps"]

HEADERS = {
    "Authorization": f"token {TOKEN}",
    "Accept": "application/vnd.github.v3+json",
    "Content-Type": "application/json",
}

def api(method, path, body=None):
    url = f"https://api.github.com/repos/{REPO}/contents/{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=HEADERS, method=method)
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise

def list_remote(dir_path):
    result = api("GET", dir_path)
    if not result or not isinstance(result, list):
        return {}
    return {f["name"]: f["sha"] for f in result}

def git_sha(path):
    """Git blob SHA for a file (same algo GitHub uses)."""
    r = subprocess.run(["git", "hash-object", path], capture_output=True, text=True, cwd=BASE)
    return r.stdout.strip()

def push_file(rel_path, local_path, remote_sha=None):
    with open(local_path, "rb") as f:
        content = base64.b64encode(f.read()).decode()
    body = {
        "message": f"sync: update {rel_path}",
        "content": content,
    }
    if remote_sha:
        body["sha"] = remote_sha
    result = api("PUT", rel_path, body)
    return result is not None

updated = 0
created = 0
failed = 0

for d in DIRS:
    print(f"\n── {d}/ ──")
    remote = list_remote(d)
    local_dir = os.path.join(BASE, d)
    local_files = sorted(f for f in os.listdir(local_dir)
                         if os.path.isfile(os.path.join(local_dir, f)))

    for fname in local_files:
        local_path = os.path.join(local_dir, fname)
        rel_path = f"{d}/{fname}"
        local_sha = git_sha(local_path)
        remote_sha = remote.get(fname)

        if remote_sha and remote_sha == local_sha:
            print(f"  ok  {fname}")
            continue

        action = "update" if remote_sha else "create"
        ok = push_file(rel_path, local_path, remote_sha)
        if ok:
            print(f"  {action}d  {fname}")
            if remote_sha:
                updated += 1
            else:
                created += 1
        else:
            print(f"  FAIL  {fname}")
            failed += 1
        time.sleep(0.3)  # be kind to the API

print(f"\nDone: {created} created, {updated} updated, {failed} failed.")
