# scrapyd_manager/scrapyd_api.py
import os
import requests
from typing import List
from logging import getLogger
from django.db.models import Q
from .cache import ttl_cache
from . import models
from datetime import datetime

logger = getLogger(__name__)


def _auth_for_node(node: models.Node):
    """返回 node 的认证信息"""
    if getattr(node, "auth", False):
        return node.username, node.password
    return None


def start_spider(spider: models.Spider) -> bool:
    """启动爬虫并返回 Job"""
    url = f"{spider.version.project.node.url}/schedule.json"
    data = {
        "project": spider.version.project.name,
        "spider": spider.name,
    }
    resp = requests.post(url, data=data, auth=_auth_for_node(spider.version.project.node), timeout=15)
    resp.raise_for_status()
    result = resp.json()
    job_id = result.get("jobid")
    if not job_id:
        raise ValueError(f"爬虫启动失败：{result}")
    return True

def start_spiders(spiders: List[models.Spider]) -> bool:
    """批量启动爬虫"""
    for spider in spiders:
        start_spider(spider)
    return True


def stop_spider(spider: models.Spider) -> List[models.Job] | None:
    """停止某个爬虫的所有任务"""
    jobs = sync_jobs(spider.version.project.node)
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
    url = f"{job.spider.version.project.node.url}/cancel.json"
    data = {
        "project": job.spider.version.project.name,
        "job": job.job_id,
    }
    resp = requests.post(url, data=data, auth=_auth_for_node(job.spider.version.project.node), timeout=15)
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
    url = f"{job.spider.version.project.node.url}/logs/{job.spider.version.project.name}/{job.spider.name}/{job.job_id}.json"
    resp = requests.get(url, auth=_auth_for_node(job.spider.version.project.node), timeout=15)
    resp.raise_for_status()
    return resp.json()


@ttl_cache()
def sync_jobs(node: models.Node) -> List[models.Job]:
    """列出节点上的所有任务并同步到数据库"""
    sync_node_projects(node)
    for project in models.Project.objects.filter(node=node):
        for version in project.versions.all():
            sync_project_version_spiders(version)
    url = f"{node.url}/listjobs.json"
    project_names = models.Project.objects.filter(node=node).values_list("name", flat=True).distinct()

    def match_job_spider(job_start_time: str, job_project_name, job_spider_name) -> models.Spider | None:
        valid_versions = []
        for project_version in models.ProjectVersion.objects.filter(project__node=node, project__name=job_project_name):
            if not project_version.version.isdigit():
                logger.warning("project version is not digit: %s@%s", project_version.version, job_project_name)
                continue
            version_datetime = datetime.fromtimestamp(int(project_version.version))
            job_datetime = datetime.strptime(job_start_time, "%Y-%m-%d %H:%M:%S.%f")
            if job_datetime > version_datetime:
                valid_versions.append(project_version)
        if not valid_versions:
            raise ValueError(f"no valid version found for job {job_project_name}@{job_spider_name} started at {job_start_time}")
        valid_versions.sort(key=lambda x: int(x.version), reverse=True)
        # 匹配spider
        for v in valid_versions:
            try:
                spider = models.Spider.objects.get(version=v, name=job_spider_name)
                break
            except models.Spider.DoesNotExist:
                continue
        else:
            raise ValueError(f"no valid project found for job {job_project_name}@{job_start_time}")
        return spider

    jobs = []
    for project_name in project_names:
        resp = requests.get(url, params={"project": project_name}, auth=_auth_for_node(node), timeout=15)
        resp.raise_for_status()
        data = resp.json()
        for status, entries in data.items():
            if status not in ("pending", "running", "finished"):
                continue

            for entry in entries:
                spider_name = entry["spider"]
                start_time = entry["start_time"]
                matched_spider = match_job_spider(start_time, project_name, spider_name)
                job = models.Job(
                    spider=matched_spider,
                    start_time=start_time,
                    job_id=entry["id"],
                    end_time=entry.get("end_time"),
                    items_url=entry.get("items_url"),
                    log_url=entry.get("log_url"),
                    pid=entry.get("pid"),
                    status=status,
                )
                job.gen_md5()
                jobs.append(job)

    deleted,  _ = models.Job.objects.filter(spider__version__project__node_id=node.id).delete()
    models.Job.objects.bulk_create(jobs)
    logger.info(f"deleted {deleted} old spiders, created {len(jobs)} new jobs for node {node}")
    return jobs


def sync_project_versions(project: models.Project):
    url = f"{project.node.url}/listversions.json"
    resp = requests.get(url, params={"project": project.name}, auth=_auth_for_node(project.node), timeout=15)
    resp.raise_for_status()
    data = resp.json()
    versions = data.get("versions", [])
    project_versions = []
    for version in versions:
        project_versions.append(models.ProjectVersion(project=project, version=version))
    models.ProjectVersion.objects.filter(~Q(version__in=versions), project=project).update(is_deleted=True)
    models.ProjectVersion.objects.bulk_create(project_versions, ignore_conflicts=True)
    logger.info(f"sync {len(project_versions)} versions for {project}")


def sync_node_projects(node: models.Node, include_version=True):
    """列出某个节点上的项目，支持是否展开版本"""
    url = f"{node.url}/listprojects.json"
    resp = requests.get(url, auth=_auth_for_node(node), timeout=15)
    resp.raise_for_status()
    data = resp.json()
    projects = data.get("projects", [])

    for project_name in projects:
        project, created = models.Project.objects.get_or_create(node=node, name=project_name)
        if include_version:
            sync_project_versions(project)
        # todo: 要删除已经移除的project
    logger.info(f"sync {len(projects)} project for {node}")


def sync_project_version_spiders(version: models.ProjectVersion) -> bool:
    """列出某个项目的爬虫"""
    if not version.is_spider_synced:
        url = f"{version.project.node.url}/listspiders.json"
        resp = requests.get(url, params={
            "project": version.project.name,
            "_version": version.version,
        }, auth=_auth_for_node(version.project.node), timeout=15)
        resp.raise_for_status()
        data = resp.json()
        spiders = data.get("spiders", [])
        results = [models.Spider(version=version, name=spider) for spider in spiders]
        models.Spider.objects.bulk_create(results)
        # 只需同步一次, 因为一个版本的spiders是不会变的
        logger.info(f"synced {len(spiders)} spiders for {version}@{version.project}")
        version.is_spider_synced = True
        version.save()
    return version.is_spider_synced


def add_version(version: models.ProjectVersion, egg_path: str):
    """部署新版本"""
    url = f"{version.project.node.url}/addversion.json"
    if not os.path.exists(egg_path):
        return {"status": "error", "reason": "egg file not found"}
    files = {"egg": open(egg_path, "rb")}
    data = {"project": version.project.name, "version": version.version}
    try:
        resp = requests.post(url, data=data, files=files, auth=_auth_for_node(version.project.node), timeout=60)
        resp.raise_for_status()
        return resp.json()
    finally:
        try:
            files["egg"].close()
        except Exception:
            pass


def delete_version(version: models.ProjectVersion):
    """删除某个版本"""
    url = f"{version.project.node.url}/delversion.json"
    data = {"project": version.project.name, "version": version.version}
    resp = requests.post(url, data=data, auth=_auth_for_node(version.project.node), timeout=15)
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
