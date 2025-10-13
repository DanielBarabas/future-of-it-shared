#!/usr/bin/env python3
import os
import sys
import re
import json
import pandas as pd
from collections import defaultdict
from github import Github
from extractor_trilingual import GitCommitAnalyzer   # reuse analyzer class

# === Config and setup ===
script_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
os.chdir(script_dir)

config_file = "config.json"
with open(config_file, 'r') as f:
    config = json.load(f)

GITHUB_TOKEN = config.get("GITHUB_TOKEN")
ORG_NAME = config.get("ORG_NAME")

g = Github(GITHUB_TOKEN)
org = g.get_organization(ORG_NAME)
repos = [repo for repo in org.get_repos() if repo.private]

output_folder = "gh_outputs"
os.makedirs(output_folder, exist_ok=True)

# === Repo metadata ===
repo_data = []
for repo in repos:
    repo_data.append({
        "name": repo.name,
        "full_name": repo.full_name,
        "private": repo.private,
        "created_at": repo.created_at,
        "default_branch": repo.default_branch
    })
pd.DataFrame(repo_data).to_csv(os.path.join(output_folder, "repositories.csv"), index=False)

# === Contributors ===
contributors_data = []
for repo in repos:
    print(f"[Contributors] {repo.name}")
    for contributor in repo.get_contributors():
        contributors_data.append({
            "repo": repo.full_name,
            "login": contributor.login,
            "contributions": contributor.contributions
        })
pd.DataFrame(contributors_data).to_csv(os.path.join(output_folder, "contributors.csv"), index=False)

# === Branches ===
branches_data = []
for repo in repos:
    print(f"[Branches] {repo.name}")
    for branch in repo.get_branches():
        branches_data.append({
            "repo": repo.full_name,
            "branch": branch.name,
            "commit_sha": branch.commit.sha
        })
pd.DataFrame(branches_data).to_csv(os.path.join(output_folder, "branches.csv"), index=False)

commits_data = []
for repo in repos:
    print(f"[Commits] {repo.name}")
    branch_map = defaultdict(list)
    commit_count = 0
    for branch in repo.get_branches():
        for commit in repo.get_commits(sha=branch.name):
            try:
                commit_count += 1
                print(f"\rProcessing commit {commit_count}: {commit.sha[:8]}", end="", flush=True)
                branch_map[commit.sha].append(branch.name)
                changed_files = []
                if commit.files:
                    for f in commit.files:
                        changed_files.append({
                            "filename": f.filename,
                            "status": f.status,
                            "additions": f.additions,
                            "deletions": f.deletions,
                            "changes": f.changes
                        })
                commits_data.append({
                    "repo": repo.full_name,
                    "sha": commit.sha,
                    "author.login": commit.author.login if commit.author else None,
                    "commit.author.date": commit.commit.author.date,
                    "commit.message": commit.commit.message,
                    "branches": branch_map.get(commit.sha, []),
                    "issues_referenced": re.findall(r"#(\d+)", commit.commit.message),
                    "additions": commit.stats.additions if commit.stats else None,
                    "deletions": commit.stats.deletions if commit.stats else None,
                    "total_changes": commit.stats.total if commit.stats else None,
                    "changed_files": json.dumps(changed_files),
                })
            except Exception as e:
                print(f"\rError on commit {commit.sha}: {e}")
                pass
    print()  # New line after repo completion
pd.DataFrame(commits_data).to_csv(os.path.join(output_folder, "commits.csv"), index=False)



# PR data
pulls_data = []
for repo in repos:
    print(f"[Pull Requests] {repo.name}")
    for pr in repo.get_pulls(state='all'):
        pulls_data.append({
            "repo": repo.full_name,
            "user.login": pr.user.login if pr.user else None,
            "created_at": pr.created_at,
            "merged_at": pr.merged_at,
            "files_impacted": pr.changed_files 
        })
pulls_df = pd.DataFrame(pulls_data)
pulls_df.to_csv(os.path.join(output_folder, "pull_requests.csv"), index=False)  

# PR comment data
pr_comments_data = []
for repo in repos:
    print(f"[PR Comments] {repo.name}")
    for pr in repo.get_pulls(state='all'):
        for comment in pr.get_issue_comments():
                pr_comments_data.append({
                    "repo": repo.full_name,
                    "pull_number": pr.number,
                    "user": comment.user.login if comment.user else None,
                    "created_at": comment.created_at,
                    "type": "issue"
                })
        for review_comment in pr.get_review_comments():
            pr_comments_data.append({
                "repo": repo.full_name,
                "pull_number": pr.number,
                "user": review_comment.user.login if review_comment.user else None,
                "created_at": review_comment.created_at,
                "position": review_comment.position,
                "type": "review"
            })
pr_comments_df = pd.DataFrame(pr_comments_data)
pr_comments_df.to_csv(os.path.join(output_folder, "pr_comments.csv"), index=False)

# Issue data
issues_data = []
for repo in repos:
    print(f"[Issues] {repo.name}")
    for issue in repo.get_issues(state='all'):
        issues_data.append({
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
issues_df = pd.DataFrame(issues_data)
issues_df.to_csv(os.path.join(output_folder, "issues.csv"), index=False)

# Issue comment data
issue_comments_data = []
for repo in repos:
    print(f"[Issue Comments] {repo.name}")
    for issue in repo.get_issues(state='all'):
        for comment in issue.get_comments():
            issue_comments_data.append({
                "repo": repo.full_name,
                "issue_number": issue.number,
                "user.login": comment.user.login if comment.user else None,
                "created_at": comment.created_at
            })
issue_comments_df = pd.DataFrame(issue_comments_data)
issue_comments_df.to_csv(os.path.join(output_folder, "issue_comments.csv"), index=False)

# === Dependency analysis per repo ===
for repo in repos:
    print(f"[Dependency Analysis] {repo.name}")
    analyzer = GitCommitAnalyzer(repo.clone_url)
    try:
        results = analyzer.analyze_all_commits()
        dep_file = os.path.join(output_folder, f"{repo.name}_deps.json")
        analyzer.save_results(results, dep_file)
        # analyzer.cleanup()
    except Exception as e:
        print(f"Dependency analysis failed for {repo.name}: {e}")



