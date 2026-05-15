"""
GitHub client — wraps REST and GraphQL APIs to create projects, labels, issues,
and set project field values.
"""

import json
import urllib.request
import urllib.error
from datetime import date, timedelta
from typing import Optional


class GitHubClient:
    REST = "https://api.github.com"
    GQL  = "https://api.github.com/graphql"

    def __init__(self, token: str, org: str):
        self.token = token
        self.org = org

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _rest(self, method: str, path: str, body: dict = None) -> dict:
        url = f"{self.REST}{path}"
        data = json.dumps(body).encode() if body else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"token {self.token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        if data:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            err = json.loads(e.read())
            raise RuntimeError(f"GitHub REST {method} {path} → {e.code}: {err.get('message', err)}") from e

    def _gql(self, query: str, variables: dict = None) -> dict:
        payload = {"query": query}
        if variables:
            payload["variables"] = variables
        data = json.dumps(payload).encode()
        req = urllib.request.Request(self.GQL, data=data, method="POST")
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req) as r:
            result = json.loads(r.read())
        if "errors" in result:
            raise RuntimeError(f"GraphQL error: {result['errors']}")
        return result["data"]

    # ── Org / repo ────────────────────────────────────────────────────────────

    def get_org_node_id(self) -> str:
        data = self._gql('{ organization(login: "%s") { id } }' % self.org)
        return data["organization"]["id"]

    def get_repo(self, repo: str) -> dict:
        return self._rest("GET", f"/repos/{self.org}/{repo}")

    def create_repo(self, name: str, description: str = "") -> dict:
        return self._rest("POST", f"/orgs/{self.org}/repos", {
            "name": name,
            "description": description,
            "private": True,
            "auto_init": True,
        })

    def repo_exists(self, repo: str) -> bool:
        try:
            self._rest("GET", f"/repos/{self.org}/{repo}")
            return True
        except RuntimeError:
            return False

    # ── Labels ────────────────────────────────────────────────────────────────

    def create_label(self, repo: str, name: str, color: str, description: str = "") -> None:
        try:
            self._rest("POST", f"/repos/{self.org}/{repo}/labels", {
                "name": name, "color": color, "description": description,
            })
        except RuntimeError as e:
            if "already_exists" in str(e) or "422" in str(e):
                pass  # label exists, fine
            else:
                raise

    # ── Issues ────────────────────────────────────────────────────────────────

    def create_issue(self, repo: str, title: str, body: str, labels: list) -> dict:
        return self._rest("POST", f"/repos/{self.org}/{repo}/issues", {
            "title": title,
            "body": body,
            "labels": labels,
        })

    # ── GitHub Projects v2 ────────────────────────────────────────────────────

    def create_project(self, title: str) -> dict:
        org_id = self.get_org_node_id()
        data = self._gql("""
            mutation($ownerId: ID!, $title: String!) {
              createProjectV2(input: { ownerId: $ownerId, title: $title }) {
                projectV2 { id number url title }
              }
            }
        """, {"ownerId": org_id, "title": title})
        return data["createProjectV2"]["projectV2"]

    def get_project_fields(self, project_id: str) -> list:
        data = self._gql("""
            query($id: ID!) {
              node(id: $id) {
                ... on ProjectV2 {
                  fields(first: 30) {
                    nodes {
                      ... on ProjectV2Field { id name }
                      ... on ProjectV2SingleSelectField {
                        id name
                        options { id name }
                      }
                    }
                  }
                }
              }
            }
        """, {"id": project_id})
        return data["node"]["fields"]["nodes"]

    def add_item_to_project(self, project_id: str, issue_url: str) -> str:
        # Get issue node id from url
        parts = issue_url.rstrip("/").split("/")
        owner, repo, _, number = parts[-4], parts[-3], parts[-2], parts[-1]
        issue_data = self._gql("""
            query($owner: String!, $repo: String!, $number: Int!) {
              repository(owner: $owner, name: $repo) {
                issue(number: $number) { id }
              }
            }
        """, {"owner": owner, "repo": repo, "number": int(number)})
        issue_node_id = issue_data["repository"]["issue"]["id"]

        data = self._gql("""
            mutation($projectId: ID!, $contentId: ID!) {
              addProjectV2ItemById(input: { projectId: $projectId, contentId: $contentId }) {
                item { id }
              }
            }
        """, {"projectId": project_id, "contentId": issue_node_id})
        return data["addProjectV2ItemById"]["item"]["id"]

    def set_single_select_field(self, project_id: str, item_id: str,
                                field_id: str, option_id: str) -> None:
        self._gql("""
            mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $optionId: String!) {
              updateProjectV2ItemFieldValue(input: {
                projectId: $projectId, itemId: $itemId, fieldId: $fieldId,
                value: { singleSelectOptionId: $optionId }
              }) { projectV2Item { id } }
            }
        """, {"projectId": project_id, "itemId": item_id,
              "fieldId": field_id, "optionId": option_id})

    def set_date_field(self, project_id: str, item_id: str,
                       field_id: str, date_str: str) -> None:
        self._gql("""
            mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $date: Date!) {
              updateProjectV2ItemFieldValue(input: {
                projectId: $projectId, itemId: $itemId, fieldId: $fieldId,
                value: { date: $date }
              }) { projectV2Item { id } }
            }
        """, {"projectId": project_id, "itemId": item_id,
              "fieldId": field_id, "date": date_str})

    def delete_project_item(self, project_id: str, item_id: str) -> None:
        self._gql("""
            mutation($projectId: ID!, $itemId: ID!) {
              deleteProjectV2Item(input: { projectId: $projectId, itemId: $itemId }) {
                deletedItemId
              }
            }
        """, {"projectId": project_id, "itemId": item_id})

    def create_single_select_field(self, project_id: str, name: str,
                                   options: list) -> dict:
        """options = [{"name": "...", "color": "BLUE", "description": "..."}]"""
        data = self._gql("""
            mutation($projectId: ID!, $name: String!, $options: [ProjectV2SingleSelectFieldOptionInput!]!) {
              createProjectV2Field(input: {
                projectId: $projectId, dataType: SINGLE_SELECT, name: $name,
                singleSelectOptions: $options
              }) {
                projectV2Field {
                  ... on ProjectV2SingleSelectField {
                    id name
                    options { id name color }
                  }
                }
              }
            }
        """, {"projectId": project_id, "name": name, "options": options})
        return data["createProjectV2Field"]["projectV2Field"]

    # ── Date helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def deadline_date(days: int = 0, weeks: int = 0) -> str:
        return (date.today() + timedelta(days=days, weeks=weeks)).isoformat()
