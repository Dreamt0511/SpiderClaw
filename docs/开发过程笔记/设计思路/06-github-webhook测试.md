@router.post("/webhook/ci")
async def github_ci_webhook(request: Request):
    """接收 GitHub Webhook CI 事件"""
    
    # 打印请求头
    print("=" * 60)
    print("收到 GitHub Webhook 请求")
    print("=" * 60)
    print("\n【请求头】:")
    for key, value in request.headers.items():
        print(f"  {key}: {value}")
    
    # 打印请求体
    payload = await request.json()
    print("\n【请求体】:")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    
    # 打印关键信息
    print("\n【关键信息提取】:")
    event_type = request.headers.get("X-GitHub-Event", "unknown")
    print(f"  事件类型: {event_type}")
    
    if "action" in payload:
        print(f"  action: {payload['action']}")
    
    if "workflow_run" in payload:
        wf = payload["workflow_run"]
        print(f"  工作流名称: {wf.get('name')}")
        print(f"  工作流状态: {wf.get('status')}")
        print(f"  工作流结论: {wf.get('conclusion')}")
        print(f"  分支: {wf.get('head_branch')}")
        print(f"  logs_url: {wf.get('logs_url')}")
    
    if "repository" in payload:
        repo = payload["repository"]
        print(f"  仓库: {repo.get('full_name')}")
        print(f"  仓库URL: {repo.get('html_url')}")
    
    if "pull_request" in payload:
        pr = payload["pull_request"]
        print(f"  PR编号: {pr.get('number')}")
        print(f"  PR标题: {pr.get('title')}")
    
    print("=" * 60)
    
    return {"status": "ok"}


上面是接收的 GitHub Webhook CI 事件的代码，你可以根据需要修改和扩展。
下面是请求的内容
============================================================
收到 GitHub Webhook 请求
============================================================

【请求头】:
  host: nape-arrive-finally.ngrok-free.dev
  user-agent: GitHub-Hookshot/d3d6c71
  content-length: 8804
  accept: */*
  content-type: application/json
  x-forwarded-for: 140.82.115.241
  x-forwarded-host: nape-arrive-finally.ngrok-free.dev
  x-forwarded-proto: https
  x-github-delivery: 4665e61c-3fba-11f1-865b-8e89d8ec4435
  x-github-event: issues
  x-github-hook-id: 610025377
  x-github-hook-installation-target-id: 1219712058
  x-github-hook-installation-target-type: repository
  accept-encoding: gzip

【请求体】:
{
  "action": "opened",
  "issue": {
    "url": "https://api.github.com/repos/Dreamt0511/AutoFix_Test_rep/issues/2",
    "repository_url": "https://api.github.com/repos/Dreamt0511/AutoFix_Test_rep",
    "labels_url": "https://api.github.com/repos/Dreamt0511/AutoFix_Test_rep/issues/2/labels{/name}",
    "comments_url": "https://api.github.com/repos/Dreamt0511/AutoFix_Test_rep/issues/2/comments",
    "events_url": "https://api.github.com/repos/Dreamt0511/AutoFix_Test_rep/issues/2/events",
    "html_url": "https://github.com/Dreamt0511/AutoFix_Test_rep/issues/2",
    "id": 4321748414,
    "node_id": "I_kwDOSLNUOs8AAAABAZilvg",
    "number": 2,
    "title": "test issue2",
    "user": {
      "login": "Dreamt0511",
      "id": 162930735,
      "node_id": "U_kgDOCbYgLw",
      "avatar_url": "https://avatars.githubusercontent.com/u/162930735?v=4",
      "gravatar_id": "",
      "url": "https://api.github.com/users/Dreamt0511",
      "html_url": "https://github.com/Dreamt0511",
      "followers_url": "https://api.github.com/users/Dreamt0511/followers",
      "following_url": "https://api.github.com/users/Dreamt0511/following{/other_user}",
      "gists_url": "https://api.github.com/users/Dreamt0511/gists{/gist_id}",
      "starred_url": "https://api.github.com/users/Dreamt0511/starred{/owner}{/repo}",
      "subscriptions_url": "https://api.github.com/users/Dreamt0511/subscriptions",
      "organizations_url": "https://api.github.com/users/Dreamt0511/orgs",
      "repos_url": "https://api.github.com/users/Dreamt0511/repos",
      "events_url": "https://api.github.com/users/Dreamt0511/events{/privacy}",
      "received_events_url": "https://api.github.com/users/Dreamt0511/received_events",
      "type": "User",
      "user_view_type": "public",
      "site_admin": false
    },
    "labels": [],
    "state": "open",
    "locked": false,
    "assignees": [],
    "milestone": null,
    "comments": 0,
    "created_at": "2026-04-24T08:47:49Z",
    "updated_at": "2026-04-24T08:47:49Z",
    "closed_at": null,
    "assignee": null,
    "author_association": "OWNER",
    "active_lock_reason": null,
    "sub_issues_summary": {
      "total": 0,
      "completed": 0,
      "percent_completed": 0
    },
    "issue_dependencies_summary": {
      "blocked_by": 0,
      "total_blocked_by": 0,
      "blocking": 0,
      "total_blocking": 0
    },
    "body": null,
    "reactions": {
      "url": "https://api.github.com/repos/Dreamt0511/AutoFix_Test_rep/issues/2/reactions",
      "total_count": 0,
      "+1": 0,
      "-1": 0,
      "laugh": 0,
      "hooray": 0,
      "confused": 0,
      "heart": 0,
      "rocket": 0,
      "eyes": 0
    },
    "timeline_url": "https://api.github.com/repos/Dreamt0511/AutoFix_Test_rep/issues/2/timeline",
    "performed_via_github_app": null,
    "state_reason": null,
    "pinned_comment": null
  },
  "repository": {
    "id": 1219712058,
    "node_id": "R_kgDOSLNUOg",
    "name": "AutoFix_Test_rep",
    "full_name": "Dreamt0511/AutoFix_Test_rep",
    "private": false,
    "owner": {
      "login": "Dreamt0511",
      "id": 162930735,
      "node_id": "U_kgDOCbYgLw",
      "avatar_url": "https://avatars.githubusercontent.com/u/162930735?v=4",
      "gravatar_id": "",
      "url": "https://api.github.com/users/Dreamt0511",
      "html_url": "https://github.com/Dreamt0511",
      "followers_url": "https://api.github.com/users/Dreamt0511/followers",
      "following_url": "https://api.github.com/users/Dreamt0511/following{/other_user}",
      "gists_url": "https://api.github.com/users/Dreamt0511/gists{/gist_id}",
      "starred_url": "https://api.github.com/users/Dreamt0511/starred{/owner}{/repo}",
      "subscriptions_url": "https://api.github.com/users/Dreamt0511/subscriptions",
      "organizations_url": "https://api.github.com/users/Dreamt0511/orgs",
      "repos_url": "https://api.github.com/users/Dreamt0511/repos",
      "events_url": "https://api.github.com/users/Dreamt0511/events{/privacy}",
      "received_events_url": "https://api.github.com/users/Dreamt0511/received_events",
      "type": "User",
      "user_view_type": "public",
      "site_admin": false
    },
    "html_url": "https://github.com/Dreamt0511/AutoFix_Test_rep",
    "description": null,
    "fork": false,
    "url": "https://api.github.com/repos/Dreamt0511/AutoFix_Test_rep",
    "forks_url": "https://api.github.com/repos/Dreamt0511/AutoFix_Test_rep/forks",
    "keys_url": "https://api.github.com/repos/Dreamt0511/AutoFix_Test_rep/keys{/key_id}",
    "collaborators_url": "https://api.github.com/repos/Dreamt0511/AutoFix_Test_rep/collaborators{/collaborator}",
    "teams_url": "https://api.github.com/repos/Dreamt0511/AutoFix_Test_rep/teams",
    "hooks_url": "https://api.github.com/repos/Dreamt0511/AutoFix_Test_rep/hooks",
    "issue_events_url": "https://api.github.com/repos/Dreamt0511/AutoFix_Test_rep/issues/events{/number}",
    "events_url": "https://api.github.com/repos/Dreamt0511/AutoFix_Test_rep/events",
    "assignees_url": "https://api.github.com/repos/Dreamt0511/AutoFix_Test_rep/assignees{/user}",
    "branches_url": "https://api.github.com/repos/Dreamt0511/AutoFix_Test_rep/branches{/branch}",
    "tags_url": "https://api.github.com/repos/Dreamt0511/AutoFix_Test_rep/tags",
    "blobs_url": "https://api.github.com/repos/Dreamt0511/AutoFix_Test_rep/git/blobs{/sha}",
    "git_tags_url": "https://api.github.com/repos/Dreamt0511/AutoFix_Test_rep/git/tags{/sha}",
    "git_refs_url": "https://api.github.com/repos/Dreamt0511/AutoFix_Test_rep/git/refs{/sha}",
    "trees_url": "https://api.github.com/repos/Dreamt0511/AutoFix_Test_rep/git/trees{/sha}",
    "statuses_url": "https://api.github.com/repos/Dreamt0511/AutoFix_Test_rep/statuses/{sha}",
    "languages_url": "https://api.github.com/repos/Dreamt0511/AutoFix_Test_rep/languages",
    "stargazers_url": "https://api.github.com/repos/Dreamt0511/AutoFix_Test_rep/stargazers",
    "contributors_url": "https://api.github.com/repos/Dreamt0511/AutoFix_Test_rep/contributors",
    "subscribers_url": "https://api.github.com/repos/Dreamt0511/AutoFix_Test_rep/subscribers",
    "subscription_url": "https://api.github.com/repos/Dreamt0511/AutoFix_Test_rep/subscription",
    "commits_url": "https://api.github.com/repos/Dreamt0511/AutoFix_Test_rep/commits{/sha}",
    "git_commits_url": "https://api.github.com/repos/Dreamt0511/AutoFix_Test_rep/git/commits{/sha}",
    "comments_url": "https://api.github.com/repos/Dreamt0511/AutoFix_Test_rep/comments{/number}",
    "issue_comment_url": "https://api.github.com/repos/Dreamt0511/AutoFix_Test_rep/issues/comments{/number}",
    "contents_url": "https://api.github.com/repos/Dreamt0511/AutoFix_Test_rep/contents/{+path}",
    "compare_url": "https://api.github.com/repos/Dreamt0511/AutoFix_Test_rep/compare/{base}...{head}",
    "merges_url": "https://api.github.com/repos/Dreamt0511/AutoFix_Test_rep/merges",
    "archive_url": "https://api.github.com/repos/Dreamt0511/AutoFix_Test_rep/{archive_format}{/ref}",
    "downloads_url": "https://api.github.com/repos/Dreamt0511/AutoFix_Test_rep/downloads",
    "issues_url": "https://api.github.com/repos/Dreamt0511/AutoFix_Test_rep/issues{/number}",
    "pulls_url": "https://api.github.com/repos/Dreamt0511/AutoFix_Test_rep/pulls{/number}",
    "milestones_url": "https://api.github.com/repos/Dreamt0511/AutoFix_Test_rep/milestones{/number}",
    "notifications_url": "https://api.github.com/repos/Dreamt0511/AutoFix_Test_rep/notifications{?since,all,participating}",
    "labels_url": "https://api.github.com/repos/Dreamt0511/AutoFix_Test_rep/labels{/name}",
    "releases_url": "https://api.github.com/repos/Dreamt0511/AutoFix_Test_rep/releases{/id}",
    "deployments_url": "https://api.github.com/repos/Dreamt0511/AutoFix_Test_rep/deployments",
    "created_at": "2026-04-24T06:34:07Z",
    "updated_at": "2026-04-24T06:55:53Z",
    "pushed_at": "2026-04-24T06:55:50Z",
    "git_url": "git://github.com/Dreamt0511/AutoFix_Test_rep.git",
    "ssh_url": "git@github.com:Dreamt0511/AutoFix_Test_rep.git",
    "clone_url": "https://github.com/Dreamt0511/AutoFix_Test_rep.git",
    "svn_url": "https://github.com/Dreamt0511/AutoFix_Test_rep",
    "homepage": null,
    "size": 4,
    "stargazers_count": 0,
    "watchers_count": 0,
    "language": null,
    "has_issues": true,
    "has_projects": true,
    "has_downloads": true,
    "has_wiki": true,
    "has_pages": false,
    "has_discussions": false,
    "forks_count": 0,
    "mirror_url": null,
    "archived": false,
    "disabled": false,
    "open_issues_count": 2,
    "license": {
      "key": "mit",
      "name": "MIT License",
      "spdx_id": "MIT",
      "url": "https://api.github.com/licenses/mit",
      "node_id": "MDc6TGljZW5zZTEz"
    },
    "allow_forking": true,
    "is_template": false,
    "web_commit_signoff_required": false,
    "has_pull_requests": true,
    "pull_request_creation_policy": "all",
    "topics": [],
    "visibility": "public",
    "forks": 0,
    "open_issues": 2,
    "watchers": 0,
    "default_branch": "main"
  },
  "sender": {
    "login": "Dreamt0511",
    "id": 162930735,
    "node_id": "U_kgDOCbYgLw",
    "avatar_url": "https://avatars.githubusercontent.com/u/162930735?v=4",
    "gravatar_id": "",
    "url": "https://api.github.com/users/Dreamt0511",
    "html_url": "https://github.com/Dreamt0511",
    "followers_url": "https://api.github.com/users/Dreamt0511/followers",
    "following_url": "https://api.github.com/users/Dreamt0511/following{/other_user}",
    "gists_url": "https://api.github.com/users/Dreamt0511/gists{/gist_id}",
    "starred_url": "https://api.github.com/users/Dreamt0511/starred{/owner}{/repo}",
    "subscriptions_url": "https://api.github.com/users/Dreamt0511/subscriptions",
    "organizations_url": "https://api.github.com/users/Dreamt0511/orgs",
    "repos_url": "https://api.github.com/users/Dreamt0511/repos",
    "events_url": "https://api.github.com/users/Dreamt0511/events{/privacy}",
    "received_events_url": "https://api.github.com/users/Dreamt0511/received_events",
    "type": "User",
    "user_view_type": "public",
    "site_admin": false
  }
}

【关键信息提取】:
  事件类型: issues
  action: opened
  仓库: Dreamt0511/AutoFix_Test_rep
  仓库URL: https://github.com/Dreamt0511/AutoFix_Test_rep
============================================================