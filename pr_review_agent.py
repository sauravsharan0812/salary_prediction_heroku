#!/usr/bin/env python3
"""
PR Review Agent - Automated code review for GitHub Pull Requests
Supports both rule-based and AI-powered (Ollama) reviews
"""

import os
import re
import sys
import json
import argparse
import requests
from urllib.parse import urlparse


class PRReviewAgent:
    def __init__(self, github_token: str, ollama_host: str = "http://localhost:11434", 
                 model: str = "qwen2.5-coder"):
        self.github_token = github_token
        self.base_url = "https://api.github.com"
        self.ollama_host = ollama_host
        self.model = model
        self.headers = {
            "Authorization": f"token {github_token}",
            "Accept": "application/vnd.github.v3+json"
        }

    def parse_pr_url(self, pr_url: str) -> dict:
        """Extract owner, repo, and PR number from URL"""
        pattern = r"github\.com/([^/]+)/([^/]+)/pull/(\d+)"
        match = re.search(pattern, pr_url)
        if not match:
            raise ValueError(f"Invalid PR URL: {pr_url}")
        return {
            "owner": match.group(1),
            "repo": match.group(2),
            "pr_number": match.group(3)
        }

    def get_pr_info(self, owner: str, repo: str, pr_number: str) -> dict:
        """Get PR details including base/head commits"""
        url = f"{self.base_url}/repos/{owner}/{repo}/pulls/{pr_number}"
        response = requests.get(url, headers=self.headers)
        response.raise_for_status()
        return response.json()

    def get_pr_diff(self, owner: str, repo: str, pr_number: str) -> str:
        """Get the diff content of the PR"""
        url = f"{self.base_url}/repos/{owner}/{repo}/pulls/{pr_number}"
        headers = {**self.headers, "Accept": "application/vnd.github.v3.diff"}
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.text

    def analyze_diff(self, diff: str) -> list:
        """Analyze the diff and extract changes"""
        changes = []
        files = diff.split("diff --git")
        
        for file_diff in files[1:]:
            lines = file_diff.split("\n")
            filename = None
            additions = []
            deletions = []
            
            for line in lines:
                if line.startswith("+++ b/"):
                    filename = line[6:].strip()
                elif line.startswith("+") and not line.startswith("+++"):
                    additions.append(line[1:])
                elif line.startswith("-") and not line.startswith("---"):
                    deletions.append(line[1:])
            
            if filename:
                changes.append({
                    "filename": filename,
                    "additions": additions,
                    "deletions": deletions
                })
        
        return changes

    def generate_review_with_ai(self, diff: str, pr_title: str = "") -> str:
        """Generate review using Ollama LLM"""
        prompt = f"""You are an expert code reviewer. Review the following GitHub PR diff and provide a thorough, helpful code review.

PR Title: {pr_title}

DIFF:
{diff}

Provide your review in markdown format with:
1. Summary of changes
2. Potential issues or bugs
3. Suggestions for improvement
4. Security considerations (if any)
5. Overall assessment

Be concise but thorough. Focus on important issues rather than style preferences."""

        try:
            response = requests.post(
                f"{self.ollama_host}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False
                },
                timeout=300
            )
            response.raise_for_status()
            result = response.json()
            return result.get("response", "AI review failed")
        except requests.exceptions.ConnectionError:
            return "Error: Cannot connect to Ollama. Make sure Ollama is running."
        except Exception as e:
            return f"Error generating AI review: {str(e)}"

    def generate_review_comment(self, changes: list) -> str:
        """Generate rule-based review comment (fallback when no AI)"""
        if not changes:
            return "No changes detected in this PR."
        
        comment = "## Code Review\n\n### Changes Overview\n\n"
        
        for change in changes:
            comment += f"**{change['filename']}**: "
            if change['additions'] and change['deletions']:
                comment += f"{len(change['deletions'])} deletions, {len(change['additions'])} additions\n"
            elif change['additions']:
                comment += f"{len(change['additions'])} additions\n"
            elif change['deletions']:
                comment += f"{len(change['deletions'])} deletions\n"
        
        comment += "\n### Analysis & Suggestions\n\n"
        
        for change in changes:
            filename = change['filename']
            additions = change['additions']
            
            if filename.endswith(".py"):
                for line in additions:
                    if "TODO" in line or "FIXME" in line:
                        comment += f"- **{filename}**: Found TODO/FIXME comment: `{line.strip()}`\n"
            
            if filename == "requirements.txt":
                for line in additions:
                    line = line.strip()
                    if line.startswith("python3") or line.startswith("python-"):
                        comment += f"- **requirements.txt**: Adding Python interpreter as dependency is unusual. Use `runtime.txt` for Heroku Python version instead.\n"
            
            if filename.endswith((".yaml", ".yml")):
                for line in additions:
                    if "image:" in line.lower() and "latest" in line.lower():
                        comment += f"- **{filename}**: Avoid using `:latest` tag for images. Pin to a specific version.\n"
        
        if len(changes) <= 3:
            comment += "\n### Summary\nThis is a small PR with minor changes. "
            if all(len(c['additions']) + len(c['deletions']) < 10 for c in changes):
                comment += "Looks good to merge!"
            else:
                comment += "Please review the changes above."
        
        return comment

    def post_general_review(self, owner: str, repo: str, pr_number: str,
                           body: str, event: str = "COMMENT") -> dict:
        """Post a general PR review"""
        url = f"{self.base_url}/repos/{owner}/{repo}/pulls/{pr_number}/reviews"
        
        payload = {
            "body": body,
            "event": event
        }
        
        response = requests.post(url, headers=self.headers, json=payload)
        response.raise_for_status()
        return response.json()

    def review_pr(self, pr_url: str, use_ai: bool = False) -> dict:
        """Main method to review a PR"""
        parsed = self.parse_pr_url(pr_url)
        owner, repo, pr_number = parsed["owner"], parsed["repo"], parsed["pr_number"]
        
        print(f"Fetching PR #{pr_number} from {owner}/{repo}...")
        
        pr_info = self.get_pr_info(owner, repo, pr_number)
        diff = self.get_pr_diff(owner, repo, pr_number)
        changes = self.analyze_diff(diff)
        
        print(f"Found {len(changes)} file(s) changed")
        
        if use_ai:
            print(f"Generating AI review using {self.model}...")
            pr_title = pr_info.get("title", "")
            comment = self.generate_review_with_ai(diff, pr_title)
        else:
            comment = self.generate_review_comment(changes)
        
        commit_id = pr_info["head"]["sha"]
        
        result = self.post_general_review(owner, repo, pr_number, comment)
        
        print(f"Review posted successfully!")
        print(f"Review URL: {result.get('html_url', 'N/A')}")
        
        return result


def main():
    parser = argparse.ArgumentParser(description="PR Review Agent - Automated code review for GitHub PRs")
    parser.add_argument("pr_url", help="GitHub PR URL (e.g., https://github.com/owner/repo/pull/123)")
    parser.add_argument("--token", "-t", help="GitHub Personal Access Token (or set GH_TOKEN env var)")
    parser.add_argument("--ai", action="store_true", help="Use AI (Ollama) for enhanced review")
    parser.add_argument("--model", "-m", default="qwen2.5-coder", help="Ollama model to use (default: qwen2.5-coder)")
    parser.add_argument("--ollama", "-o", default="http://localhost:11434", help="Ollama host URL")
    
    args = parser.parse_args()
    
    github_token = args.token or os.environ.get("GH_TOKEN")
    if not github_token:
        print("Error: GitHub token required. Use --token or set GH_TOKEN environment variable.")
        print("Generate a token at: https://github.com/settings/tokens")
        sys.exit(1)
    
    agent = PRReviewAgent(
        github_token, 
        ollama_host=args.ollama,
        model=args.model
    )
    
    try:
        agent.review_pr(args.pr_url, use_ai=args.ai)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
