#!/usr/bin/env python3
import os
import sys
import re
import json
import subprocess
from collections import defaultdict
from typing import Dict, List, Tuple, Iterable

import pandas as pd
from github import Github

from extractor_trilingual import GitCommitAnalyzer

# -------------------------
# Local git helpers
# -------------------------

def run(cmd: List[str], cwd: str = None) -> str:
    res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\nSTDERR: {res.stderr.strip()}")
    return res.stdout

def ensure_local_clone(repo_name: str, clone_url: str, dest_root: str, token: str = None) -> str:
    dest = os.path.join(dest_root, repo_name)
    auth_url = clone_url
    if token and clone_url.startswith("https://"):
        auth_url = clone_url.replace("https://", f"https://{token}@")
    if os.path.exists(dest) and os.path.isdir(os.path.join(dest, ".git")):
        run(["git", "remote", "set-url", "origin", auth_url], cwd=dest)
        run(["git", "fetch", "--all", "--prune"], cwd=dest)
    else:
        os.makedirs(dest_root, exist_ok=True)
        run(["git", "clone", "--no-tags", "--quiet", auth_url, dest])
        run(["git", "fetch", "--all", "--prune"], cwd=dest)
    return dest

def list_all_branches(repo_path: str) -> List[str]:
    """
    Return both local and remote-tracking branches (origin/*), excluding symbolic origin/HEAD.
    """
    out = run(
        ["git", "for-each-ref", "--format=%(refname:short)", "refs/heads", "refs/remotes/origin"],
        cwd=repo_path
    )
    branches = [ln.strip() for ln in out.splitlines() if ln.strip()]
    branches = [b for b in branches if b != "origin/HEAD"]
    return branches

def commits_by_branch(repo_path: str, branches: Iterable[str]) -> Tuple[Dict[str, List[str]], List[str]]:
    """
    Build a mapping from commit SHA -> list of branch names that contain it,
    and return a deduped list of all commits reachable from all refs (git rev-list --all).
    """
    sha_to_branches: Dict[str, List[str]] = defaultdict(list)
    for br in branches:
        out = run(["git", "rev-list", br], cwd=repo_path)
        for sha in out.splitlines():
            if sha:
                sha_to_branches[sha].append(br)

    # Use the full all-refs traversal order; do NOT restrict to seen SHAs.
    all_unique = run(["git", "rev-list", "--all"], cwd=repo_path).splitlines()
    # keep only real SHAs (defensive, though rev-list emits SHAs)
    all_unique = [s for s in all_unique if s]
    return sha_to_branches, all_unique

def parse_numstat(repo_path: str, sha: str):
    out = run(["git", "show", "--numstat", "--format=", sha], cwd=repo_path)
    total_adds = 0
    total_dels = 0
    per_file: Dict[str, Tuple[int, int]] = {}
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        adds, dels, fname = parts
        try:
            adds_i = int(adds) if adds.isdigit() else 0
            dels_i = int(dels) if dels.isdigit() else 0
        except Exception:
            adds_i, dels_i = 0, 0
        per_file[fname] = (adds_i, dels_i)
        total_adds += adds_i
        total_dels += dels_i
    return total_adds, total_dels, per_file

def parse_name_status(repo_path: str, sha: str):
    out = run(["git", "diff-tree", "--no-commit-id", "--name-status", "-r", sha], cwd=repo_path)
    add_del_map = parse_numstat(repo_path, sha)[2]
    changed = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            status = parts[0]
            filename = parts[-1]  # for renames, last is new name
            adds, dels = add_del_map.get(filename, (0, 0))
            changed.append({
                "filename": filename,
                "status": status,
                "additions": adds,
                "deletions": dels,
                "changes": adds + dels,
            })
    return changed

def get_all_commit_data(repo_path: str, commits: List[str]) -> Dict[str, Dict]:
    """Get all commit data in three batched git calls instead of 3*N calls per commit."""
    if not commits:
        return {}
    
    # First call: get commit headers (include merge commits to match commits_by_branch)
    fmt = "%H|%an|%ae|%ad|%s"
    cmd = ["git", "log", f"--format={fmt}", "--date=iso-strict"] + commits
    header_out = run(cmd, cwd=repo_path)
    
    # Second call: get numstat (include merge commits)
    cmd = ["git", "log", "--format=%H", "--numstat"] + commits
    numstat_out = run(cmd, cwd=repo_path)
    
    # Third call: get name-status (include merge commits)
    cmd = ["git", "log", "--format=%H", "--name-status"] + commits
    status_out = run(cmd, cwd=repo_path)
    
    # Parse headers first
    result = {}
    for line in header_out.splitlines():
        if "|" in line and len(line.split("|")) >= 5:
            parts = line.split("|", 4)  # Split into max 5 parts, keeping any extra pipes in the subject
            sha = parts[0]
            result[sha] = {
                "author_name": parts[1],
                "author_email": parts[2], 
                "author_date": parts[3],
                "subject": parts[4],  # This can contain additional pipes
                "changed_files": [],
                "total_adds": 0,
                "total_dels": 0
            }
    
    # Parse numstat (store per-SHA numstat data)
    current_sha = None
    sha_numstat = {}
    
    for line in numstat_out.splitlines():
        line = line.strip()
        if not line:
            continue
        # Check if this is a commit SHA (40 hex chars)
        if len(line) == 40 and all(c in '0123456789abcdef' for c in line.lower()):
            current_sha = line
            sha_numstat[current_sha] = {}
        elif current_sha and "\t" in line:
            parts = line.split("\t")
            if len(parts) == 3:
                # Numstat line: additions, deletions, filename
                adds, dels, fname = parts
                try:
                    adds_i = int(adds) if adds.isdigit() else 0
                    dels_i = int(dels) if dels.isdigit() else 0
                except:
                    adds_i, dels_i = 0, 0
                sha_numstat[current_sha][fname] = (adds_i, dels_i)
                if current_sha in result:
                    result[current_sha]["total_adds"] += adds_i
                    result[current_sha]["total_dels"] += dels_i
    
    # Parse name-status and combine with numstat
    current_sha = None
    
    for line in status_out.splitlines():
        line = line.strip()
        if not line:
            continue
        # Check if this is a commit SHA (40 hex chars)
        if len(line) == 40 and all(c in '0123456789abcdef' for c in line.lower()):
            current_sha = line
        elif current_sha and current_sha in result and "\t" in line:
            parts = line.split("\t")
            if len(parts) == 2:
                # Name-status line: status, filename
                status, filename = parts
                # Get numstat data for this file from stored data
                adds, dels = sha_numstat.get(current_sha, {}).get(filename, (0, 0))
                result[current_sha]["changed_files"].append({
                    "filename": filename,
                    "status": status,
                    "additions": adds,
                    "deletions": dels,
                    "changes": adds + dels,
                })
    
    return result

def commit_header(repo_path: str, sha: str):
    fmt = "%H|%an|%ae|%ad|%s"
    out = run(["git", "show", "-s", f"--format={fmt}", "--date=iso-strict", sha], cwd=repo_path)
    parts = out.strip().split("|", 4)
    if len(parts) != 5:
        return ("", "", "", "")
    _, author_name, author_email, author_date, subject = parts
    return (author_name, author_email, author_date, subject)

# -------------------------
# Main
# -------------------------

def main():
    script_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    os.chdir(script_dir)

    config_file = "config.json"
    with open(config_file, 'r') as f:
        config = json.load(f)

    GITHUB_TOKEN = config.get("GITHUB_TOKEN")
    ORG_NAME = config.get("ORG_NAME")

    g = Github(GITHUB_TOKEN)
    org = g.get_organization(ORG_NAME)

    # private-only
    repos = [repo for repo in org.get_repos() if repo.private]

    output_folder = "gh_outputs"
    os.makedirs(output_folder, exist_ok=True)

    local_root = os.path.join(output_folder, "local_repos")
    os.makedirs(local_root, exist_ok=True)

    # repositories.csv
    repo_rows = [{
        "name": repo.name,
        "full_name": repo.full_name,
        "private": repo.private,
        "created_at": repo.created_at,
        "default_branch": repo.default_branch
    } for repo in repos]
    pd.DataFrame(repo_rows).to_csv(os.path.join(output_folder, "repositories.csv"), index=False)

    # contributors.csv
    contributors_rows = []
    for repo in repos:
        print(f"[Contributors] {repo.name}")
        for contributor in repo.get_contributors():
            contributors_rows.append({
                "repo": repo.full_name,
                "login": contributor.login if contributor else None,
                "contributions": getattr(contributor, "contributions", None)
            })
    pd.DataFrame(contributors_rows).to_csv(os.path.join(output_folder, "contributors.csv"), index=False)

    # branches.csv (reference)
    branches_rows = []
    for repo in repos:
        print(f"[Branches] {repo.name}")
        for branch in repo.get_branches():
            branches_rows.append({
                "repo": repo.full_name,
                "branch": branch.name,
                "commit_sha": branch.commit.sha
            })
    pd.DataFrame(branches_rows).to_csv(os.path.join(output_folder, "branches.csv"), index=False)

    # commits.csv (local git, de-duped, ALL refs)
    commits_rows = []
    for repo in repos:
        print(f"[Commits: local git] {repo.name}")
        try:
            repo_path = ensure_local_clone(repo.name, repo.clone_url, local_root, token=GITHUB_TOKEN)

            local_branches = list_all_branches(repo_path)
            if not local_branches:
                run(["git", "checkout", repo.default_branch], cwd=repo_path)
                local_branches = list_all_branches(repo_path)

            branch_map, all_commits = commits_by_branch(repo_path, local_branches)

            # Batch process all commit data 
            print(f"  processing {len(all_commits)} commits in batch...")
            commit_data = get_all_commit_data(repo_path, all_commits)
            
            individual_calls = 0
            for i, sha in enumerate(all_commits, 1):
                try:
                    data = commit_data.get(sha, {})
                    if data:
                        # Use all batched data
                        author_name = data["author_name"]
                        author_email = data["author_email"]
                        author_date = data["author_date"]
                        subject = data["subject"]
                        changed_files = data["changed_files"]
                        total_adds = data["total_adds"]
                        total_dels = data["total_dels"]
                    else:
                        # Fallback to individual calls
                        individual_calls += 1
                        if individual_calls <= 5:  # Log first few failures for debugging
                            print(f"\r  DEBUG: batch miss for {sha[:8]} (#{individual_calls})")
                            print(f"    Total parsed commits in batch: {len(commit_data)}")
                            print(f"    Expected total commits: {len(all_commits)}")
                            # Check if this SHA is in the beginning or end of the list
                            sha_pos = all_commits.index(sha) if sha in all_commits else -1
                            print(f"    SHA position in list: {sha_pos}")
                        author_name, author_email, author_date, subject = commit_header(repo_path, sha)
                        changed_files = parse_name_status(repo_path, sha)
                        total_adds, total_dels, _ = parse_numstat(repo_path, sha)
                    
                    # Running counter with carriage return
                    print(f"\r  processed: {i}/{len(all_commits)} (individual: {individual_calls})", end="", flush=True)
                    
                    commits_rows.append({
                        "repo": repo.full_name,
                        "sha": sha,
                        "author.name": author_name,
                        "author.email": author_email,
                        "commit.author.date": author_date,
                        "commit.message": subject,
                        "branches": branch_map.get(sha, []),
                        "issues_referenced": re.findall(r"#(\d+)", subject or ""),
                        "additions": total_adds,
                        "deletions": total_dels,
                        "total_changes": (total_adds + total_dels),
                        "changed_files": json.dumps(changed_files),
                    })
                except Exception as e:
                    print(f"\r    error on {sha[:8]}: {e}")
                    continue
            
            # Complete the progress line
            print()  # newline after carriage return progress
        except Exception as e:
            print(f"  failed on repo {repo.name}: {e}")
            continue

    pd.DataFrame(commits_rows).to_csv(os.path.join(output_folder, "commits.csv"), index=False)

    # pull_requests.csv
    pulls_rows = []
    for repo in repos:
        print(f"[Pull Requests] {repo.name}")
        for pr in repo.get_pulls(state='all'):
            pulls_rows.append({
                "repo": repo.full_name,
                "number": pr.number,
                "user.login": pr.user.login if pr.user else None,
                "created_at": pr.created_at,
                "merged_at": pr.merged_at,
                "files_impacted": getattr(pr, "changed_files", None)
            })
    pd.DataFrame(pulls_rows).to_csv(os.path.join(output_folder, "pull_requests.csv"), index=False)

    # pr_comments.csv
    pr_comments_rows = []
    for repo in repos:
        print(f"[PR Comments] {repo.name}")
        for pr in repo.get_pulls(state='all'):
            for comment in pr.get_issue_comments():
                pr_comments_rows.append({
                    "repo": repo.full_name,
                    "pull_number": pr.number,
                    "user": comment.user.login if comment.user else None,
                    "created_at": comment.created_at,
                    "type": "issue"
                })
            for review_comment in pr.get_review_comments():
                pr_comments_rows.append({
                    "repo": repo.full_name,
                    "pull_number": pr.number,
                    "user": review_comment.user.login if review_comment.user else None,
                    "created_at": review_comment.created_at,
                    "position": review_comment.position,
                    "type": "review"
                })
    pd.DataFrame(pr_comments_rows).to_csv(os.path.join(output_folder, "pr_comments.csv"), index=False)

    # issues.csv
    issues_rows = []
    for repo in repos:
        print(f"[Issues] {repo.name}")
        for issue in repo.get_issues(state='all'):
            issues_rows.append({
                "repo": repo.full_name,
                "number": issue.number,
                "title": issue.title,
                "user.login": issue.user.login if issue.user else None,
                "assignees": [a.login for a in issue.assignees],
                "comments_count": issue.comments,
                "state": issue.state,
                "created_at": issue.created_at,
                "closed_at": issue.closed_at
            })
    pd.DataFrame(issues_rows).to_csv(os.path.join(output_folder, "issues.csv"), index=False)

    # issue_comments.csv
    issue_comments_rows = []
    for repo in repos:
        print(f"[Issue Comments] {repo.name}")
        for issue in repo.get_issues(state='all'):
            for comment in issue.get_comments():
                issue_comments_rows.append({
                    "repo": repo.full_name,
                    "issue_number": issue.number,
                    "user.login": comment.user.login if comment.user else None,
                    "created_at": comment.created_at
                })
    pd.DataFrame(issue_comments_rows).to_csv(os.path.join(output_folder, "issue_comments.csv"), index=False)

    # dependency analysis (local, reuses analyzer)
    for repo in repos:
        print(f"[Dependency Analysis] {repo.name}")
        analyzer = GitCommitAnalyzer(repo.clone_url)
        try:
            results = analyzer.analyze_all_commits()
            dep_file = os.path.join(output_folder, f"{repo.name}_deps.json")
            analyzer.save_results(results, dep_file)
        except Exception as e:
            print(f"Dependency analysis failed for {repo.name}: {e}")

if __name__ == "__main__":
    main()
