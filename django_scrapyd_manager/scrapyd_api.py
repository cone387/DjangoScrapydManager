# scrapyd_manager/scrapyd_api.py
import os
import requests
from typing import List
from logging import getLogger
from .cache import ttl_cache
from . import models
from datetime import datetime

logger = getLogger(__name__)


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

    job = models.Job(
        spider=spider,
        job_id=job_id,
        start_time=models.datetime.now(),
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


@ttl_cache()
def get_job_info(job: models.Job) -> dict:
    """获取某个任务的详细信息"""
    url = f"{job.spider.project.node.url}/logs/{job.spider.project.name}/{job.spider.name}/{job.job_id}.json"
    resp = requests.get(url, auth=_auth_for_node(job.spider.project.node), timeout=15)
    resp.raise_for_status()
    return resp.json()


@ttl_cache()
def list_jobs(node: models.Node) -> List[models.Job]:
    """列出节点上的所有任务并同步到数据库"""
    list_node_projects(node)
    for project in models.Project.objects.filter(node=node):
        list_project_spiders(project)
    url = f"{node.url}/listjobs.json"
    projects = models.Project.objects.filter(node=node)

    def match_job_spider(job_start_time: str, job_project_name, job_spider_name) -> models.Spider | None:
        valid_projects = []
        for p in projects.filter(name=job_project_name):
            if not p.version.isdigit():
                logger.warning("project version is not digit: %s@%s", p.name, p.version)
                continue
            version_datetime = datetime.fromtimestamp(int(p.version))
            job_datetime = datetime.strptime(job_start_time, "%Y-%m-%d %H:%M:%S.%f")
            if job_datetime > version_datetime:
                valid_projects.append(p)
        if not valid_projects:
            raise ValueError(f"no valid version found for job {job_project_name}@{job_spider_name} started at {job_start_time}")
        valid_projects.sort(key=lambda x: int(x.version), reverse=True)
        target_project = valid_projects[0]
        # 匹配spider
        spider = models.Spider.objects.get(project=target_project, name=job_spider_name)
        return spider


    jobs = []
    for project in projects:
        resp = requests.get(url, params={"project": project.name}, auth=_auth_for_node(node), timeout=15)
        resp.raise_for_status()
        data = resp.json()
        for status, entries in data.items():
            if status not in ("pending", "running", "finished"):
                continue

            for entry in entries:
                spider_name = entry["spider"]
                project_name = entry["project"]
                start_time = entry["start_time"]
                matched_spider = match_job_spider(start_time, project_name, spider_name)
                job = models.Job(
                    spider=matched_spider,
                    start_time=start_time,
                    job_id=entry.get("id"),
                    end_time=entry.get("end_time"),
                    items_url=entry.get("items_url"),
                    log_url=entry.get("log_url"),
                    pid=entry.get("pid"),
                    status=status,
                )
                jobs.append(job)

    deleted,  _ = models.Job.objects.filter(spider__project__node=node).delete()
    models.Job.objects.bulk_create(jobs)
    logger.info(f"deleted {deleted} old spiders, created {len(jobs)} new jobs for node {node}")
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

    deleted, _ = models.Project.objects.filter(node=node).delete()
    models.Project.objects.bulk_create(results)
    logger.info(f"deleted {deleted} old projects, created {len(projects)} new projects for node {node}")
    return results


@ttl_cache()
def list_project_spiders(project: models.Project) -> List[models.Spider]:
    """列出某个项目的爬虫"""
    url = f"{project.node.url}/listspiders.json"
    resp = requests.get(url, params={
        "project": project.name,
        "_version": project.version,
    }, auth=_auth_for_node(project.node), timeout=15)
    resp.raise_for_status()
    data = resp.json()
    spiders = data.get("spiders", [])
    results = [models.Spider(project=project, name=spider) for spider in spiders]
    deleted, _ = models.Spider.objects.filter(project=project).delete()
    models.Spider.objects.bulk_create(results)
    logger.info(f"deleted {deleted} old spiders, created {len(spiders)} new spiders for project {project}")
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
