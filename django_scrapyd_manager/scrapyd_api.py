# scrapyd_manager/scrapyd_api.py
import os
import requests
from typing import Optional, Dict, Any, List
from .cache import ttl_cache



def _auth_for_node(node):
    if getattr(node, "auth", False):
        return (node.username, node.password)
    return None


def schedule_spider(node, project: str, spider: str, **kwargs) -> Dict[str, Any]:
    """
    POST /schedule.json
    kwargs can include: jobid, setting, args (as dict) etc.
    """
    url = f"{node.url}/schedule.json"
    data = {"project": project, "spider": spider}
    # Flatten args if passed as dict under kwargs['args']
    if "args" in kwargs and isinstance(kwargs["args"], dict):
        for k, v in kwargs["args"].items():
            data[k] = v
        kwargs.pop("args")
    data.update({k: v for k, v in kwargs.items() if v is not None})
    try:
        resp = requests.post(url, data=data, auth=_auth_for_node(node), timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"status": "error", "reason": str(e)}


def cancel_spider(node, project: str, job: str) -> Dict[str, Any]:
    url = f"{node.url}/cancel.json"
    data = {"project": project, "job": job}
    try:
        resp = requests.post(url, data=data, auth=_auth_for_node(node), timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"status": "error", "reason": str(e)}


def list_jobs(node, project: str) -> List[Dict[str, Any]]:
    url = f"{node.url}/listjobs.json"
    resp = requests.get(url, params={"project": project}, auth=_auth_for_node(node), timeout=15)
    resp.raise_for_status()
    data = resp.json()
    pending_jobs = data.get("pending", [])
    running_jobs = data.get("running", [])
    finished_jobs = data.get("finished", [])
    jobs = []
    for job in pending_jobs:
        jobs.append({
            "job_id": job.pop("id"),
            "spider": job["spider"],
            "status": "pending",
            **job,
        })
    for job in running_jobs:
        jobs.append({
            "job_id": job.pop("id"),
            "spider": job["spider"],
            "status": "running",
            **job,
        })
    for job in finished_jobs:
        jobs.append({
            "job_id": job.pop("id"),
            "spider": job["spider"],
            "status": "finished",
            **job,
        })
    return jobs


@ttl_cache()
def list_versions(node, project: str) -> Dict[str, Any]:
    url = f"{node.url}/listversions.json?project={project}"
    resp = requests.get(url, auth=_auth_for_node(node), timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return data["versions"]


@ttl_cache()
def list_projects(node, include_version=True) -> List[Dict[str, Any]]:
    url = f"{node.url}/listprojects.json"
    resp = requests.get(url, auth=_auth_for_node(node), timeout=15)
    resp.raise_for_status()
    data = resp.json()
    projects = data["projects"]
    project_models = []
    if include_version:
        for project in projects:
            versions = list_versions(node, project)
            for version in versions:
                project_model = {
                    "name": project,
                    "version": version,
                    "node_id": node.id,
                }
                project_models.append(project_model)
    return project_models

@ttl_cache()
def list_spiders(node, project: str) -> List[Dict[str, Any]]:
    url = f"{node.url}/listspiders.json"
    resp = requests.get(url, params={"project": project}, auth=_auth_for_node(node), timeout=15)
    resp.raise_for_status()
    data = resp.json()
    spiders = data["spiders"]
    spider_models = []
    for spider in spiders:
        spider_models.append({
            "name": spider,
        })
    return spider_models # Filter out empty strings

def add_version(node, project: str, version: str, egg_path: str) -> Dict[str, Any]:
    """
    Upload egg to scrapyd via /addversion.json
    egg_path must be an existing file path.
    """
    url = f"{node.url}/addversion.json"
    if not os.path.exists(egg_path):
        return {"status": "error", "reason": "egg file not found"}
    files = {"egg": open(egg_path, "rb")}
    data = {"project": project, "version": version}
    try:
        resp = requests.post(url, data=data, files=files, auth=_auth_for_node(node), timeout=60)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"status": "error", "reason": str(e)}
    finally:
        try:
            files["egg"].close()
        except Exception:
            pass
