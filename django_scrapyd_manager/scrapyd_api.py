# scrapyd_manager/scrapyd_api.py
import requests
from django.utils import timezone
from typing import List
from logging import getLogger
from .cache import django_ttl_cache
from . import models
from django.db.models import Q
from typing import Protocol, Iterable
from datetime import datetime
from django.conf import settings


class SpiderGroupLike(Protocol):
    name: str
    code: str | None
    kwargs: dict
    settings: dict
    resolved_spiders: Iterable[models.Spider]


logger = getLogger(__name__)


class ScrapydResponseError(Exception):
    pass


class NodesSyncError(Exception):
    pass


def _auth_for_node(node: models.Node):
    """返回 node 的认证信息"""
    if getattr(node, "auth", False):
        return node.username, node.password
    return None


def start_spider(spider: models.Spider) -> str:
    """启动爬虫并返回 Job"""
    url = f"{spider.version.project.node.url}/schedule.json"
    kwargs = spider.kwargs.copy()
    for k, v in list(kwargs.items()):
        if k.startswith("__"):
            kwargs.pop(k)
    data = {
        "project": spider.version.project.name,
        "spider": spider.name,
        "setting": [f"{k}={v}" for k, v in spider.settings.items()],
        "jobid": spider.job_id,
        **kwargs,
    }
    resp = requests.post(url, data=data, auth=_auth_for_node(spider.version.project.node), timeout=15)
    resp.raise_for_status()
    result = resp.json()
    job_id = result.get("jobid")
    if not job_id:
        raise ValueError(f"爬虫启动失败：{result}")
    return job_id

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
    url = f"{job.node.url}/cancel.json"
    data = {
        "project": job.project.name,
        "job": job.job_id,
    }
    resp = requests.post(url, data=data, auth=_auth_for_node(job.node), timeout=15)
    resp.raise_for_status()
    result = resp.json()
    if result.get("status") == "ok":
        job.status = models.JobStatus.FINISHED
        job.end_time = timezone.now()
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


def start_spider_group(group: SpiderGroupLike) -> List[models.Job]:
    """启动任务组里的所有爬虫"""
    spiders = group.resolved_spiders
    if not spiders:
        raise ValueError("group下面没有爬虫")
    job_ids = []
    for spider in spiders:
        spider.kwargs["__group__"] = group.code
        spider.kwargs.update(group.kwargs)
        spider.settings.update(group.settings)
        job_id = start_spider(spider)
        job_ids.append(job_id)
    return job_ids


def stop_spider_group(group: models.SpiderGroup) -> List[models.Job]:
    """停止任务组里的所有爬虫"""
    jobs = []
    for spider in group.resolved_spiders:
        job = stop_spider(spider)
        if job:
            jobs.extend(job)
    return jobs


@django_ttl_cache()
def get_job_info(job: models.Job) -> dict:
    """获取某个任务的详细信息"""
    url = f"{job.node.url}/logs/{job.project.name}/{job.spider.name}/{job.job_id}.json"
    resp = requests.get(url, auth=_auth_for_node(job.node), timeout=15)
    resp.raise_for_status()
    return resp.json()


@django_ttl_cache()
def sync_jobs(node: models.Node) -> List[models.Job]:
    """列出节点上的所有任务并同步到数据库"""
    url = f"{node.url}/listjobs.json"
    jobs = []
    models.Job.objects.filter(node=node, status=models.JobStatus.PENDING).delete()
    for project in node.projects.all():
        resp = requests.get(url, params={"project": project.name}, auth=_auth_for_node(node), timeout=15)
        resp.raise_for_status()
        data = resp.json()
        for status, entries in data.items():
            if status not in ("pending", "running", "finished"):
                continue
            for entry in entries:
                spider_name = entry["spider"]
                start_time = entry.get("start_time")
                if start_time and settings.USE_TZ:
                    start_time = timezone.make_aware(datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S.%f"))
                else:
                    start_time = timezone.now()
                end_time = entry.get("end_time")
                if end_time and settings.USE_TZ:
                    end_time = timezone.make_aware(datetime.strptime(end_time, "%Y-%m-%d %H:%M:%S.%f"))
                job = models.Job(
                    node=node,
                    project=project,
                    spider=models.SpiderRegistry.objects.get(name=spider_name),
                    start_time=start_time,
                    job_id=entry["id"],
                    end_time=end_time,
                    items_url=entry.get("items_url"),
                    log_url=entry.get("log_url"),
                    pid=entry.get("pid"),
                    status=status,
                )
                job.gen_md5()
                jobs.append(job)

    models.Job.objects.bulk_create(jobs, ignore_conflicts=True)
    logger.info(f"{len(jobs)} jobs synced for {node}")
    return jobs


@django_ttl_cache()
def sync_project_versions(project: models.Project):
    url = f"{project.node.url}/listversions.json"
    resp = requests.get(url, params={"project": project.name}, auth=_auth_for_node(project.node), timeout=15)
    resp.raise_for_status()
    data = resp.json()
    versions = data.get("versions", [])
    project_versions = []
    for version in versions:
        project_versions.append(models.ProjectVersion(project=project, version=version, sync_status=models.SyncStatus.SUCCESS, scrapyd_exists=True))
    # models.ProjectVersion.objects.bulk_create(
    #     project_versions, update_conflicts=True, unique_fields=("project", "version"), update_fields=["sync_status", "scrapyd_exists"])
    # mysql不支持update_conflicts=True, 所以这里先插入, 再筛选更新
    models.ProjectVersion.objects.bulk_create(project_versions, ignore_conflicts=True)
    models.ProjectVersion.objects.filter(project=project, version__in=versions).update(sync_status=models.SyncStatus.SUCCESS, scrapyd_exists=True)
    models.ProjectVersion.objects.filter(~Q(version__in=versions), project=project).update(scrapyd_exists=False)
    logger.info(f"sync {len(project_versions)} versions for {project}")


@django_ttl_cache()
def sync_node_projects(node: models.Node, include_version=True):
    """列出某个节点上的项目，支持是否展开版本"""
    url = f"{node.url}/listprojects.json"
    resp = requests.get(url, auth=_auth_for_node(node), timeout=5)
    resp.raise_for_status()
    data = resp.json()
    scrapyd_projects = data.get("projects", [])
    projects = []
    for project_name in scrapyd_projects:
        project, created = models.Project.objects.update_or_create(node=node, name=project_name, defaults={"sync_status": models.SyncStatus.SUCCESS, "scrapyd_exists": True})
        projects.append(project)
        if include_version:
            sync_project_versions(project)
    models.Project.objects.filter(~Q(name__in=scrapyd_projects), node=node).update(scrapyd_exists=False)
    logger.info(f"sync {len(scrapyd_projects)} project for {node}")
    return projects


def sync_project_version_spiders(version: models.ProjectVersion) -> bool:
    """列出某个项目的爬虫"""
    if version.scrapyd_exists and (not version.is_spider_synced or version.spiders.count() == 0):
        url = f"{version.project.node.url}/listspiders.json"
        resp = requests.get(url, params={
            "project": version.project.name,
            "_version": version.version,
        }, auth=_auth_for_node(version.project.node), timeout=5)
        resp.raise_for_status()
        data = resp.json()
        spiders = data.get("spiders", [])
        spider_registries = [models.SpiderRegistry(name=spider) for spider in spiders]
        models.SpiderRegistry.objects.bulk_create(spider_registries, ignore_conflicts=True)

        results = [models.Spider(version=version, name=spider, registry_id=spider) for spider in spiders]
        models.Spider.objects.bulk_create(results)
        # 只需同步一次, 因为一个版本的spiders是不会变的
        logger.info(f"synced {len(spiders)} spiders for {version}@{version.project}")
        version.is_spider_synced = True
        version.save()
    return version.is_spider_synced


def add_version(version: models.ProjectVersion):
    """部署新版本"""
    url = f"{version.project.node.url}/addversion.json"
    if not version.egg_file:
        raise Exception("egg_file is not set")
    files = {"egg": version.egg_file.open()}
    data = {"project": version.project.name, "version": version.version}
    resp = requests.post(url, data=data, files=files, auth=_auth_for_node(version.project.node), timeout=15)
    resp.raise_for_status()
    return resp.json()


def delete_version(version: models.ProjectVersion):
    """删除某个版本"""
    url = f"{version.project.node.url}/delversion.json"
    data = {"project": version.project.name, "version": version.version}
    resp = requests.post(url, data=data, auth=_auth_for_node(version.project.node), timeout=15)
    resp.raise_for_status()
    ret = resp.json()
    if ret["status"] != "ok":
        raise ScrapydResponseError(ret["message"])


def delete_project(project: models.Project):
    """删除整个项目"""
    url = f"{project.node.url}/delproject.json"
    data = {"project": project.name}
    resp = requests.post(url, data=data, auth=_auth_for_node(project.node), timeout=15)
    resp.raise_for_status()
    ret = resp.json()
    if ret["status"] != "success":
        raise ScrapydResponseError(ret["message"])


def daemon_status(node: models.Node, timeout=3) -> dict:
    """获取节点的 daemon 状态"""
    url = f"{node.url}/daemonstatus.json"
    resp = requests.get(url, auth=_auth_for_node(node), timeout=timeout)
    resp.raise_for_status()
    return resp.json()


@django_ttl_cache()
def sync_nodes(with_jobs=False):
    nodes = models.Node.objects.all()
    available_nodes = []
    error_nodes = []
    for node in nodes:
        try:
            sync_node_projects(node, include_version=True)
            available_nodes.append(node)
        except Exception as e:
            logger.error(f"同步节点 {node} 失败: {e}")
            error_nodes.append(node)
    for version in models.ProjectVersion.objects.filter(project__node__in=available_nodes, scrapyd_exists=True, project__scrapyd_exists=True):
        sync_project_version_spiders(version)

    if with_jobs:
        for node in available_nodes:
            sync_jobs(node)

    if error_nodes:
        return f"节点{[node.name for node in available_nodes]}同步成功, 节点{[node.name for node in error_nodes]}同步失败"
    return ""



