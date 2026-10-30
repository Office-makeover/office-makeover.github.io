import os
import base64
import json
import urllib.request
import urllib.error

# ==========================================
# CONFIGURATION
# ==========================================
# 1. Generate a Personal Access Token 
#    - Fine-grained: Needs 'Contents: Read/Write' AND 'Administration: Read/Write'
#    - Classic: Needs 'repo' scope
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "YOUR_GITHUB_TOKEN_HERE")

# 2. Your GitHub Username or Organization Name
OWNER = os.environ.get("GITHUB_OWNER", "YOUR_USERNAME_OR_ORG")

# 3. Set to True to target all repos, or False for public-only
INCLUDE_PRIVATE = True

USER_AGENT = "GitHub-Batch-Deployer/1.1"

def github_api_request(url, method="GET", data=None):
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28"
    }
    json_data = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=json_data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as response:
            if response.status == 204:
                return {}
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8")
        print(f"  [API Error {e.code}] {url}: {err_body}")
        return None
    except Exception as e:
        print(f"  [Error] {url}: {e}")
        return None

def get_all_repositories(owner):
    repos = []
    page = 1
    while True:
        # Try user endpoint first
        url = f"https://api.github.com/users/{owner}/repos?per_page=100&page={page}"
        data = github_api_request(url)
        
        # If empty or fails, fallback to organization endpoint
        if not data or not isinstance(data, list) or len(data) == 0:
            url_org = f"https://api.github.com/orgs/{owner}/repos?per_page=100&page={page}"
            data = github_api_request(url_org)
            if not data or not isinstance(data, list) or len(data) == 0:
                break
        
        for repo in data:
            if not INCLUDE_PRIVATE and repo.get("private", False):
                continue
            if repo.get("archived", False):
                continue
            repos.append(repo.get("full_name"))
        
        if len(data) < 100:
            break
        page += 1
    return repos

def create_or_update_file(repo_full_name, file_path, content, commit_message):
    api_url = f"https://api.github.com/repos/{repo_full_name}/contents/{file_path}"
    
    existing = github_api_request(api_url, method="GET")
    sha = existing.get("sha") if isinstance(existing, dict) and "sha" in existing else None
    
    encoded_content = base64.b64encode(content.encode("utf-8")).decode("utf-8")
    
    payload = {
        "message": commit_message,
        "content": encoded_content
    }
    if sha:
        payload["sha"] = sha
        
    result = github_api_request(api_url, method="PUT", data=payload)
    return result is not None

def enable_github_pages(repo_full_name):
    """
    Configures GitHub Pages for the repository to build via GitHub Actions.
    Requires Administration permissions on the Personal Access Token.
    """
    api_url = f"https://api.github.com/repos/{repo_full_name}/pages"
    payload = {"build_type": "workflow"}
    
    # 1. Try to create Pages site configuration
    result = github_api_request(api_url, method="POST", data=payload)
    
    # 2. If it fails (e.g. 409 Conflict if it already exists), try to update it
    if result is None:
        result = github_api_request(api_url, method="PUT", data=payload)
        
    return result is not None

def main():
    if GITHUB_TOKEN == "YOUR_GITHUB_TOKEN_HERE" or OWNER == "YOUR_USERNAME_OR_ORG":
        print("ERROR: Please configure your GITHUB_TOKEN and GITHUB_OWNER in the script or environment variables.")
        return

    print(f"Fetching repositories for owner: {OWNER}...")
    repos = get_all_repositories(OWNER)
    print(f"Found {len(repos)} active repositories.")

    workflow_file_path = "generate-pages-workflow.yml"
    script_file_path = "generate_pages.py"

    if not os.path.exists(workflow_file_path) or not os.path.exists(script_file_path):
        print("ERROR: Make sure 'generate-pages-workflow.yml' and 'generate_pages.py' are in the same directory.")
        return

    with open(workflow_file_path, "r", encoding="utf-8") as f:
        workflow_content = f.read()

    with open(script_file_path, "r", encoding="utf-8") as f:
        script_content = f.read()

    for repo in repos:
        print(f"\nDeploying to repository: {repo}...")
        
        print("  - Pushing .github/workflows/generate-pages-workflow.yml...")
        success_wf = create_or_update_file(repo, ".github/workflows/generate-pages-workflow.yml", workflow_content, "Add automated editorial hub workflow")
        
        print("  - Pushing generate_pages.py...")
        success_sc = create_or_update_file(repo, "generate_pages.py", script_content, "Add automated editorial hub generator script")
        
        print("  - Configuring GitHub Pages (Build Type: GitHub Actions)...")
        success_pages = enable_github_pages(repo)
        
        if success_wf and success_sc and success_pages:
            print(f"  SUCCESS: Deployed to and configured Pages for {repo}")
        else:
            print(f"  WARNING: Some steps failed for {repo} (Check API errors above)")

    print("\nBatch deployment and Pages configuration complete!")

if __name__ == "__main__":
    main()
