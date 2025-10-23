
#!/usr/bin/env python3
import os
import re
import json
import subprocess
from dataclasses import dataclass
from typing import Dict, List, Set, Tuple, Optional

# -------------------------
# Utilities
# -------------------------

def run(cmd: List[str], cwd: Optional[str] = None) -> str:
    res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\nSTDERR: {res.stderr.strip()}")
    return res.stdout

def ensure_local_clone(repo_url: str, dest_root: str, token: Optional[str]) -> str:
    """
    Clone or fetch a repo locally using HTTPS. If a token is provided, authenticate clone.
    Returns the local repo path.
    """
    name = repo_url.rstrip("/").split("/")[-1]
    if name.endswith(".git"):
        name = name[:-4]
    dest = os.path.join(dest_root, name)
    auth_url = repo_url
    if token and auth_url.startswith("https://"):
        auth_url = auth_url.replace("https://", f"https://{token}@")
    if os.path.isdir(os.path.join(dest, ".git")):
        run(["git", "remote", "set-url", "origin", auth_url], cwd=dest)
        run(["git", "fetch", "--all", "--prune"], cwd=dest)
    else:
        os.makedirs(dest_root, exist_ok=True)
        run(["git", "clone", "--no-tags", "--quiet", auth_url, dest])
        run(["git", "fetch", "--all", "--prune"], cwd=dest)
    return dest

# -------------------------
# Analyzer
# -------------------------

@dataclass
class CommitResult:
    sha: str
    files: List[str]
    typescript_imports: Dict[str, List[str]]
    javascript_imports: Dict[str, List[str]]
    swift_imports: Dict[str, List[str]]

class GitCommitAnalyzer:
    def __init__(self, repo_url: str, workdir: str = "deps_work"):
        self.repo_url = repo_url
        self.workdir = workdir
        self.token = os.environ.get("GITHUB_TOKEN")  # optional for private repos
        os.makedirs(self.workdir, exist_ok=True)
        self.repo_path = ensure_local_clone(repo_url, self.workdir, self.token)

    # ------------ commit enumeration ------------
    def list_all_commits(self) -> List[str]:
        out = run(["git", "rev-list", "--all"], cwd=self.repo_path)
        return [ln.strip() for ln in out.splitlines() if ln.strip()]

    # ------------ file helpers ------------
    def list_files_at_commit(self, sha: str) -> List[str]:
        out = run(["git", "ls-tree", "-r", "--name-only", sha], cwd=self.repo_path)
        return [ln.strip() for ln in out.splitlines() if ln.strip()]

    def get_file_content_at_commit(self, sha: str, path: str) -> Optional[str]:
        try:
            out = run(["git", "show", f"{sha}:{path}"], cwd=self.repo_path)
            return out
        except Exception:
            return None

    # ------------ type guards ------------
    @staticmethod
    def is_typescript_file(p: str) -> bool:
        p = p.lower()
        return p.endswith(".ts") or p.endswith(".tsx")

    @staticmethod
    def is_javascript_file(p: str) -> bool:
        p = p.lower()
        return p.endswith(".js") or p.endswith(".jsx")

    @staticmethod
    def is_swift_file(p: str) -> bool:
        return p.lower().endswith(".swift")

    # ------------ import extractors ------------
    ts_import_re = re.compile(
        r"(?:(?:import\s+[^;]+?from\s+['\"]([^'\"]+)['\"])|(?:import\s+['\"]([^'\"]+)['\"]))",
        re.MULTILINE,
    )
    js_import_re = ts_import_re  # same for most cases
    swift_import_re = re.compile(r"^\s*import\s+([A-Za-z0-9_\.]+)", re.MULTILINE)

    @classmethod
    def extract_typescript_imports(cls, text: str) -> Set[str]:
        imports: Set[str] = set()
        for m in cls.ts_import_re.finditer(text):
            mod = m.group(1) or m.group(2)
            if mod:
                imports.add(mod)
        return imports

    @classmethod
    def extract_javascript_imports(cls, text: str) -> Set[str]:
        imports: Set[str] = set()
        for m in cls.js_import_re.finditer(text):
            mod = m.group(1) or m.group(2)
            if mod:
                imports.add(mod)
        # CommonJS require()
        for m in re.finditer(r"require\(\s*['\"]([^'\"]+)['\"]\s*\)", text):
            imports.add(m.group(1))
        return imports

    @classmethod
    def extract_swift_imports(cls, text: str) -> Set[str]:
        return set(m.group(1) for m in cls.swift_import_re.finditer(text))

    # ------------ main analysis ------------
    def analyze_commit(self, sha: str) -> CommitResult:
        files = self.list_files_at_commit(sha)
        ts_imports: Dict[str, List[str]] = {}
        js_imports: Dict[str, List[str]] = {}
        swift_imports: Dict[str, List[str]] = {}

        for fp in files:
            try:
                if self.is_typescript_file(fp):
                    content = self.get_file_content_at_commit(sha, fp)
                    if content:
                        mods = self.extract_typescript_imports(content)
                        if mods:
                            ts_imports[fp] = sorted(mods)
                elif self.is_javascript_file(fp):
                    content = self.get_file_content_at_commit(sha, fp)
                    if content:
                        mods = self.extract_javascript_imports(content)
                        if mods:
                            js_imports[fp] = sorted(mods)
                elif self.is_swift_file(fp):
                    content = self.get_file_content_at_commit(sha, fp)
                    if content:
                        mods = self.extract_swift_imports(content)
                        if mods:
                            swift_imports[fp] = sorted(mods)
            except Exception:
                continue

        return CommitResult(
            sha=sha,
            files=files,
            typescript_imports=ts_imports,
            javascript_imports=js_imports,
            swift_imports=swift_imports,
        )

    def get_batched_file_data(self, commits: List[str]) -> Dict[str, Dict]:
        """Get file listings for all commits in one git call."""
        if not commits:
            return {}
        
        # Get all files for all commits at once
        cmd = ["git", "log", "--name-only", "--format=%H"] + commits
        out = run(cmd, cwd=self.repo_path)
        
        result = {}
        current_sha = None
        
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            # Check if this is a commit SHA (40 hex chars)
            if len(line) == 40 and all(c in '0123456789abcdef' for c in line):
                current_sha = line
                result[current_sha] = {"files": []}
            elif current_sha and line:
                # Filter for relevant file types early
                if (line.lower().endswith(('.ts', '.tsx', '.js', '.jsx', '.swift'))):
                    result[current_sha]["files"].append(line)
        
        return result

    def analyze_all_commits(self, limit: Optional[int] = None) -> List[Dict]:
        commits = self.list_all_commits()
        if limit:
            commits = commits[:limit]
        
        # Batch get file listings for all commits
        print(f"[deps] getting file listings for {len(commits)} commits in batch...")
        batched_files = self.get_batched_file_data(commits)
        
        results: List[Dict] = []
        for i, sha in enumerate(commits, 1):
            if i % 500 == 0:
                print(f"[deps] processed {i}/{len(commits)} commits...")
            
            # Use batched file data if available, otherwise fallback
            batch_data = batched_files.get(sha, {})
            if batch_data and "files" in batch_data:
                relevant_files = batch_data["files"]
            else:
                print(f"[deps] fallback to individual file listing for {sha[:8]}")
                all_files = self.list_files_at_commit(sha)
                relevant_files = [f for f in all_files if (
                    self.is_typescript_file(f) or self.is_javascript_file(f) or self.is_swift_file(f)
                )]
            
            # Process imports for filtered files
            ts_imports: Dict[str, List[str]] = {}
            js_imports: Dict[str, List[str]] = {}
            swift_imports: Dict[str, List[str]] = {}

            for fp in relevant_files:
                try:
                    content = self.get_file_content_at_commit(sha, fp)
                    if content:
                        if self.is_typescript_file(fp):
                            mods = self.extract_typescript_imports(content)
                            if mods:
                                ts_imports[fp] = sorted(mods)
                        elif self.is_javascript_file(fp):
                            mods = self.extract_javascript_imports(content)
                            if mods:
                                js_imports[fp] = sorted(mods)
                        elif self.is_swift_file(fp):
                            mods = self.extract_swift_imports(content)
                            if mods:
                                swift_imports[fp] = sorted(mods)
                except Exception:
                    continue

            results.append({
                "sha": sha,
                "files": relevant_files,
                "typescript_imports": ts_imports,
                "javascript_imports": js_imports,
                "swift_imports": swift_imports,
            })
        return results

    @staticmethod
    def save_results(results: List[Dict], path: str) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(results, f, indent=2)

    def cleanup(self) -> None:
        pass

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Local commit dependency analysis for TS/JS/Swift files.")
    parser.add_argument("repo", help="HTTPS clone URL")
    parser.add_argument("--out", default="deps.json", help="Output JSON file")
    parser.add_argument("--limit", type=int, default=None, help="Optional commit limit for testing")
    args = parser.parse_args()

    analyzer = GitCommitAnalyzer(args.repo)
    results = analyzer.analyze_all_commits(limit=args.limit)
    GitCommitAnalyzer.save_results(results, args.out)
    print(f"Wrote {args.out} with {len(results)} commit entries.")
