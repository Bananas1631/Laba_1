"""Создаёт в GitHub метки, вехи, эпики и задачи по файлу planning/backlog.yml.

Использование (нужен GitHub CLI `gh` с авторизацией):
    python scripts/create_backlog.py OWNER/REPO [--assignee LOGIN]

Эпики и задачи создаются как Issues; задачи привязываются к эпику
как sub-issues; подзадачи записываются чек-листом в тело задачи.
Задачи с `done: true` сразу закрываются. Результат сохраняется
в planning/issues.json.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LABELS = [
    "accessibility",
    "bug", "documentation", "duplicate", "enhancement", "good first issue",
    "help wanted", "invalid", "question", "wontfix",
]


def gh(method: str, path: str, payload: dict | None = None, ok404: bool = False):
    cmd = ["gh", "api", "-X", method, path, "-H", "Accept: application/vnd.github+json"]
    if payload is not None:
        cmd += ["--input", "-"]
    for attempt in range(4):
        res = subprocess.run(
            cmd, input=json.dumps(payload) if payload is not None else None,
            capture_output=True, text=True,
        )
        if res.returncode == 0:
            return json.loads(res.stdout) if res.stdout.strip() else None
        if ok404 and "404" in (res.stderr + res.stdout):
            return None
        if "secondary rate limit" in (res.stderr + res.stdout).lower():
            time.sleep(20 * (attempt + 1))
            continue
        break
    raise RuntimeError(f"{method} {path}: {res.stdout} {res.stderr}")


def task_body(task: dict, epic_no: int, team: dict) -> str:
    subtasks = []
    for s in task["subtasks"]:
        if s.startswith("[x] "):
            subtasks.append(f"- [x] {s[4:]}")
        else:
            subtasks.append(f"- [{'x' if task.get('done') else ' '}] {s}")
    return "\n".join([
        f"**Эпик:** #{epic_no}",
        f"**Пункт ТЗ:** {task['tz']}",
        f"**Ответственный:** {team[task['owner']]}",
        "",
        "### Описание",
        task["description"],
        "",
        "### Подзадачи",
        *subtasks,
        "",
        "### Критерии приёмки",
        task["acceptance"],
    ])


def epic_body(epic: dict, team: dict, children: list[tuple[int, str, bool]] | None = None) -> str:
    lines = [
        f"**Пункт ТЗ:** {epic['tz']}",
        f"**Ответственный:** {team[epic['owner']]}",
        "",
        "### Описание",
        epic["description"],
        "",
        "### Задачи",
    ]
    if children:
        lines += [f"- [{'x' if done else ' '}] #{no}" for no, _title, done in children]
    else:
        lines.append("_добавляются как sub-issues_")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("repo")
    ap.add_argument("--assignee", help="GitHub-логин, которому назначить задачи участника --owner")
    ap.add_argument("--owner", default="Шаталин")
    args = ap.parse_args()
    repo = args.repo
    plan = yaml.safe_load((ROOT / "planning/backlog.yml").read_text(encoding="utf-8"))
    team = plan["team"]

    # 1. Метки
    for name in DEFAULT_LABELS:
        gh("DELETE", f"repos/{repo}/labels/{name}", ok404=True)
    existing = {l["name"] for l in gh("GET", f"repos/{repo}/labels?per_page=100")}
    for lab in plan["labels"]:
        if lab["name"] not in existing:
            gh("POST", f"repos/{repo}/labels", lab)
    print(f"Метки: {len(plan['labels'])}")

    # 2. Вехи
    ms_no = {}
    current = {m["title"]: m["number"] for m in gh("GET", f"repos/{repo}/milestones?state=all")}
    for ms in plan["milestones"]:
        if ms["title"] in current:
            ms_no[ms["key"]] = current[ms["title"]]
            continue
        m = gh("POST", f"repos/{repo}/milestones", {
            "title": ms["title"], "description": ms["description"],
            "due_on": f"{ms['due']}T23:59:59Z",
        })
        ms_no[ms["key"]] = m["number"]
    print(f"Вехи: {len(ms_no)}")

    # 3. Эпики и задачи
    result = []

    def assignees(owner: str) -> list[str]:
        return [args.assignee] if args.assignee and owner == args.owner else []

    for epic in plan["epics"]:
        e = gh("POST", f"repos/{repo}/issues", {
            "title": epic["title"],
            "body": epic_body(epic, team),
            "labels": ["эпик", *epic["labels"], f"исп: {epic['owner']}"],
            "milestone": ms_no[epic["milestone"]],
            "assignees": assignees(epic["owner"]),
        })
        children = []
        for task in epic["tasks"]:
            t = gh("POST", f"repos/{repo}/issues", {
                "title": task["title"],
                "body": task_body(task, e["number"], team),
                "labels": ["задача", *task["labels"], f"исп: {task['owner']}"],
                "milestone": ms_no[epic["milestone"]],
                "assignees": assignees(task["owner"]),
            })
            gh("POST", f"repos/{repo}/issues/{e['number']}/sub_issues", {"sub_issue_id": t["id"]})
            done = bool(task.get("done"))
            children.append((t["number"], task["title"], done))
            result.append({
                "type": "задача", "number": t["number"], "title": task["title"],
                "epic": e["number"], "milestone": epic["milestone"], "owner": task["owner"],
                "labels": task["labels"], "subtasks": len(task["subtasks"]),
                "done": done, "url": t["html_url"],
            })
            time.sleep(0.5)
        gh("PATCH", f"repos/{repo}/issues/{e['number']}", {"body": epic_body(epic, team, children)})
        epic_done = all(d for _, _, d in children)
        result.append({
            "type": "эпик", "number": e["number"], "title": epic["title"],
            "milestone": epic["milestone"], "owner": epic["owner"],
            "tasks": [n for n, _, _ in children], "done": epic_done, "url": e["html_url"],
        })
        print(f"#{e['number']} {epic['title']}: {len(children)} задач")

    # 4. Закрыть выполненные
    for item in result:
        if item["done"]:
            gh("POST", f"repos/{repo}/issues/{item['number']}/comments", {
                "body": "Выполнено в рамках лабораторной работы №1: результат загружен в репозиторий (см. README и папку docs/).",
            })
            gh("PATCH", f"repos/{repo}/issues/{item['number']}", {"state": "closed", "state_reason": "completed"})

    result.sort(key=lambda r: r["number"])
    (ROOT / "planning/issues.json").write_text(
        json.dumps({"repo": repo, "milestones": ms_no, "issues": result}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Создано Issues: {len(result)}, закрыто: {sum(r['done'] for r in result)}")


if __name__ == "__main__":
    sys.exit(main())
