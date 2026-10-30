import os
import json
import urllib.request
import urllib.error

# Use the exact same Classic Token you used for the last step
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "YOUR_GITHUB_TOKEN_HERE")
USER_AGENT = "GitHub-Batch-Trigger/1.0"

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
                return True
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code != 404: # Ignore 404s for repos that don't have the workflow
            print(f"  [API Error {e.code}] {url}")
        return False
    except Exception as e:
        print(f"  [Error] {url}: {e}")
        return False

def get_all_accessible_repositories():
    repos = []
    page = 1
    while True:
        url = f"https://api.github.com/user/repos?per_page=100&page={page}&affiliation=owner,organization_member"
        data = github_api_request(url)
        if not data or not isinstance(data, list) or len(data) == 0:
            break
        for repo in data:
            if repo.get("archived", False): continue
            repos.append({
                "full_name": repo.get("full_name"),
                "default_branch": repo.get("default_branch", "main")
            })
        if len(data) < 100:
            break
        page += 1
    return repos

def trigger_workflow(repo_full_name, branch_name):
    # Endpoint to manually trigger a workflow dispatch event
    api_url = f"https://api.github.com/repos/{repo_full_name}/actions/workflows/generate-pages-workflow.yml/dispatches"
    payload = {"ref": branch_name}
    
    result = github_api_request(api_url, method="POST", data=payload)
    return result

def main():
    if not GITHUB_TOKEN or GITHUB_TOKEN == "YOUR_GITHUB_TOKEN_HERE":
        print("ERROR: Please configure your GITHUB_TOKEN.")
        return

    print("Fetching repositories...")
    repos = get_all_accessible_repositories()
    
    print(f"Triggering site generation workflows...")
    success_count = 0

    for repo in repos:
        repo_name = repo["full_name"]
        branch = repo["default_branch"]
        
        # Trigger the workflow
        success = trigger_workflow(repo_name, branch)
        if success:
            print(f"  SUCCESS: Started build for {repo_name}")
            success_count += 1
            
    print(f"\nFinished! Successfully triggered {success_count} site builds.")
    print("Please wait 2-3 minutes for GitHub's servers to finish generating the HTML and publishing the pages.")

if __name__ == "__main__":
    main()
