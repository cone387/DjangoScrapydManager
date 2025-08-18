# scrapyd_manager/scrapyd_api.py
import os
import requests
from typing import List
from django.utils.dateparse import parse_datetime
from .cache import ttl_cache
from . import models


def _auth_for_node(node: models.Node):
    """返回 node 的认证信息"""
    if getattr(node, "auth", False):
        return node.username, node.password
    return None


def start_spider(spider: models.Spider) -> models.Job | None:
    """启动爬虫并返回 Job"""
    url = f"{spider.project.node.url}/schedule.json"
    data = {
        "project": spider.project.name,
        "spider": spider.name,
    }
    resp = requests.post(url, data=data, auth=_auth_for_node(spider.project.node), timeout=15)
    resp.raise_for_status()
    result = resp.json()
    job_id = result.get("jobid")

    if not job_id:
        return None

    job = models.Job.objects.create(
        spider=spider,
        job_id=job_id,
        start_time=parse_datetime(result.get("start_time")) or models.datetime.now(),
        log_url=f"/logs/{spider.project.name}/{spider.name}/{job_id}.log",
        status="pending",
    )
    return job


def start_spiders(spiders: List[models.Spider]) -> List[models.Job]:
    """批量启动爬虫"""
    jobs = []
    for spider in spiders:
        job = start_spider(spider)
        if job:
            jobs.append(job)
    return jobs


def stop_spider(spider: models.Spider) -> List[models.Job] | None:
    """停止某个爬虫的所有任务"""
    jobs = list_jobs(spider.project.node)
    target_jobs = [j for j in jobs if j.spider == spider]
    stopped = []
    for job in target_jobs:
        stopped_job = stop_job(job)
        if stopped_job:
            stopped.append(stopped_job)
    return stopped or None


def stop_spiders(spiders: List[models.Spider]) -> List[models.Job] | None:
    """批量停止爬虫"""
    jobs = []
    for spider in spiders:
        job = stop_spider(spider)
        if job:
            jobs.extend(job)
    return jobs or None


def stop_job(job: models.Job) -> models.Job | None:
    """停止单个任务"""
    url = f"{job.spider.project.node.url}/cancel.json"
    data = {
        "project": job.spider.project.name,
        "job": job.job_id,
    }
    resp = requests.post(url, data=data, auth=_auth_for_node(job.spider.project.node), timeout=15)
    resp.raise_for_status()
    result = resp.json()
    if result.get("status") == "ok":
        job.status = "stopped"
        job.end_time = models.datetime.now()
        job.save(update_fields=["status", "end_time", "update_time"])
        return job
    return None


def stop_jobs(jobs: List[models.Job]) -> List[models.Job]:
    """批量停止任务"""
    stopped_jobs = []
    for job in jobs:
        stopped_job = stop_job(job)
        if stopped_job:
            stopped_jobs.append(stopped_job)
    return stopped_jobs


def start_spider_group(group: models.SpiderGroup) -> List[models.Job]:
    """启动任务组里的所有爬虫"""
    jobs = []
    for spider in group.spiders.all():
        job = start_spider(spider)
        if job:
            jobs.append(job)
    return jobs


def stop_spider_group(group: models.SpiderGroup) -> List[models.Job]:
    """停止任务组里的所有爬虫"""
    jobs = []
    for spider in group.spiders.all():
        job = stop_spider(spider)
        if job:
            jobs.extend(job)
    return jobs


def list_jobs(node: models.Node) -> List[models.Job]:
    """列出节点上的所有任务并同步到数据库"""
    url = f"{node.url}/listjobs.json"
    projects = models.Project.objects.filter(node=node).values_list("name", flat=True)

    jobs = []
    for project_name in projects:
        resp = requests.get(url, params={"project": project_name}, auth=_auth_for_node(node), timeout=15)
        resp.raise_for_status()
        data = resp.json()

        for status, entries in data.items():
            if status not in ("pending", "running", "finished"):
                continue
            for entry in entries:
                job_id = entry.get("id")
                spider_name = entry.get("spider")

                spider = models.Spider.objects.filter(
                    project__node=node, project__name=project_name, name=spider_name
                ).first()
                if not spider:
                    continue

                job, _ = models.Job.objects.update_or_create(
                    job_id=job_id,
                    spider=spider,
                    defaults={
                        "status": status,
                        "pid": entry.get("pid"),
                        "start_time": parse_datetime(entry.get("start_time")) or models.datetime.now(),
                        "end_time": parse_datetime(entry.get("end_time")),
                        "log_url": entry.get("log_url"),
                        "items_url": entry.get("items_url"),
                    },
                )
                jobs.append(job)
    return jobs


@ttl_cache()
def list_project_versions(project: models.Project) -> List[str]:
    """列出某个项目的版本"""
    url = f"{project.node.url}/listversions.json"
    resp = requests.get(url, params={"project": project.name}, auth=_auth_for_node(project.node), timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return data.get("versions", [])


@ttl_cache()
def list_node_projects(node: models.Node, include_version=True) -> List[models.Project]:
    """列出某个节点上的项目，支持是否展开版本"""
    url = f"{node.url}/listprojects.json"
    resp = requests.get(url, auth=_auth_for_node(node), timeout=15)
    resp.raise_for_status()
    data = resp.json()
    projects = data.get("projects", [])
    results = []

    if include_version:
        for project_name in projects:
            versions = list_project_versions(models.Project(id=1, node=node, name=project_name))
            for version in versions:
                results.append(models.Project(node=node, name=project_name, version=version))
    else:
        results = [models.Project(node=node, name=project_name) for project_name in projects]
    models.Project.objects.bulk_create(results, ignore_conflicts=True)
    return results


@ttl_cache()
def list_project_spiders(project: models.Project) -> List[models.Spider]:
    """列出某个项目的爬虫"""
    url = f"{project.node.url}/listspiders.json"
    resp = requests.get(url, params={"project": project.name}, auth=_auth_for_node(project.node), timeout=15)
    resp.raise_for_status()
    data = resp.json()
    spiders = data.get("spiders", [])
    results = [models.Spider(project=project, name=spider) for spider in spiders]
    models.Spider.objects.bulk_create(results, ignore_conflicts=True)
    return results


def add_version(project: models.Project, egg_path: str):
    """部署新版本"""
    url = f"{project.node.url}/addversion.json"
    if not os.path.exists(egg_path):
        return {"status": "error", "reason": "egg file not found"}
    files = {"egg": open(egg_path, "rb")}
    data = {"project": project.name, "version": project.version}
    try:
        resp = requests.post(url, data=data, files=files, auth=_auth_for_node(project.node), timeout=60)
        resp.raise_for_status()
        return resp.json()
    finally:
        try:
            files["egg"].close()
        except Exception:
            pass


def delete_version(project: models.Project):
    """删除某个版本"""
    url = f"{project.node.url}/delversion.json"
    data = {"project": project.name, "version": project.version}
    resp = requests.post(url, data=data, auth=_auth_for_node(project.node), timeout=15)
    resp.raise_for_status()
    return resp.json()


def delete_project(project: models.Project):
    """删除整个项目"""
    url = f"{project.node.url}/delproject.json"
    data = {"project": project.name}
    resp = requests.post(url, data=data, auth=_auth_for_node(project.node), timeout=15)
    resp.raise_for_status()
    return resp.json()


def daemon_status(node: models.Node) -> dict:
    """获取节点的 daemon 状态"""
    url = f"{node.url}/daemonstatus.json"
    resp = requests.get(url, auth=_auth_for_node(node), timeout=15)
    resp.raise_for_status()
    return resp.json()


def node_info(node: models.Node) -> dict:
    """聚合节点的基本信息（状态、项目、版本、爬虫）"""
    return {
        "status": daemon_status(node),
        "projects": list_node_projects(node, include_version=True),
    }
