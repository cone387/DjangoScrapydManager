import traceback
from typing import Iterable
from django_scrapyd_manager import models, scrapyd_api, signals
from django.utils import timezone
from django_sched.sched import BaseScheduler


class InvalidVersionError(Exception):
    pass


def node_has_project(node: models.Node, project: models.Project) -> bool:
    scrapyd_projects = scrapyd_api.sync_node_projects(node, include_version=False)
    for scrapyd_project in scrapyd_projects:
        if scrapyd_project == project:
            return True
    return False


def deploy_project_version(project_version: models.ProjectVersion):
    scrapyd_api.add_version(project_version)
    scrapyd_api.sync_project_version_spiders(project_version)


def missing_spiders_jobs_on_node(required_spiders: Iterable[models.Spider], node: models.Node) -> Iterable[models.Spider]:
    node_jobs = scrapyd_api.sync_jobs(node)
    job_ids = []
    missing_spiders = []
    for job in node_jobs:
        if job.status != models.JobStatus.FINISHED:
            job_ids.append(job.job_id)

    for required_spider in required_spiders:
        spider_fp = required_spider.fp
        for job_id in job_ids:
            if spider_fp in job_id:
                break
        else:
            missing_spiders.append(required_spider)
    return missing_spiders


def resolve_spiders_from_registries(registry_spiders: Iterable[models.SpiderRegistry], group: models.SpiderGroup) -> Iterable[models.Spider]:
    version = group.resolved_version
    resolved_spiders = version.spiders.filter(
        registry__in=registry_spiders,
    )
    resolved_spider_names = resolved_spiders.values_list("name", flat=True)
    registry_spider_names = [x.name for x in registry_spiders]
    for spider_name in registry_spider_names:
        if spider_name not in resolved_spider_names:
            raise RuntimeError(f"Spider {spider_name} not in {resolved_spider_names}")
    return resolved_spiders


class GuardSpiderGroup:

    def __init__(self, group: models.SpiderGroup, missing_spiders: Iterable[models.Spider]):
        self.resolved_spiders = missing_spiders
        self.name = group.name
        self.code = group.code
        self.kwargs = group.kwargs
        self.settings = group.settings


def get_group_publishable_version(spider_group: models.SpiderGroup) -> models.ProjectVersion:
    version = spider_group.version
    if version is not None:
        if version.egg_file:
            return version
        else:
            raise InvalidVersionError(f"{spider_group.project}/{version.version}没有egg文件")
    version = spider_group.project.versions.filter(
        egg_file__isnull=False,
    ).order_by("-create_time", "-version").first()
    if version is None:
        raise InvalidVersionError(f"项目({spider_group.project})没有可用的带egg版本")
    return version


class GuardianScheduler(BaseScheduler):
    interval = 30

    def guard_object(self, spider_guardian: models.Guardian):
        logs = []
        node = spider_guardian.spider_group.node
        if not node_has_project(node, spider_guardian.spider_group.project):
            log = models.GuardianLog(
                guardian=spider_guardian,
                node=node,
                group=spider_guardian.spider_group,
                action=models.GuardianAction.PUBLISH_VERSION,
                reason=f"node({node})上没有该项目:{spider_guardian.spider_group.project}"
            )
            try:
                version = get_group_publishable_version(spider_guardian.spider_group)
            except InvalidVersionError as e:
                log.success = False
                log.message = str(e)
            else:
                try:
                    deploy_project_version(version)
                except Exception as e:
                    log.success = False
                    log.message = traceback.format_exc()
                    self.logger.exception(e)
            log.save()
            logs.append(log)
        guard_spiders = spider_guardian.spider_group.resolved_spiders
        missing_spiders = missing_spiders_jobs_on_node(guard_spiders, node)

        if missing_spiders:
            for spider in missing_spiders:
                # 依次记录每个爬虫的启动状态
                log = models.GuardianLog(
                    guardian=spider_guardian,
                    node=node,
                    spider=spider,
                    spider_name=spider.name,
                    group=spider_guardian.spider_group,
                    action=models.GuardianAction.START_SPIDER,
                    reason=f"{node}/{spider_guardian.spider_group.project}/{spider_guardian.spider_group.name}的爬虫{spider.name}没有运行"
                )
                try:
                    guard_spider_group = GuardSpiderGroup(group=spider_guardian.spider_group, missing_spiders=[spider])
                    scrapyd_api.start_spider_group(guard_spider_group)
                except Exception as e:
                    log.success = False
                    log.message = traceback.format_exc()
                    self.logger.exception(e)
                log.save()
                logs.append(log)
        return logs

    def guard_objects(self, objects: list[models.Guardian] = None):
        objects = objects or models.Guardian.objects.filter(enable=True).prefetch_related("spider_group",
                                                                                          "spider_group__node",
                                                                                          "spider_group__project")
        signals.guard_objects_started.send(sender=self, objects=objects)
        result_mapping = {}
        for obj in objects:
            name = (obj.description or "")[:20] or f"爬虫组守护{obj.spider_group.name}"
            try:
                logs = self.guard_object(obj)
                result_mapping[name] = {
                    "success": True,
                    "logs": logs
                }
                obj.last_action = logs[0].action if logs else "ok"
                signals.guard_obj_success.send(sender=self, model=obj, logs=logs)
            except Exception as e:
                result_mapping[name] = {
                    "success": False,
                    "error": str(e)
                }
                self.logger.exception(e)
                obj.last_action = f"error: {e}"[:200]
                signals.guard_obj_error.send(sender=self, model=obj, exception=e)
            obj.last_check = timezone.now()
            obj.save()
        return result_mapping

    # ANSI 颜色
    COLORS = {
        "GREEN": "\033[32m",
        "RED": "\033[31m",
        "YELLOW": "\033[33m",
        "CYAN": "\033[36m",
        "RESET": "\033[0m",
    }

    def log_guard_results(self, result_mapping: dict):
        """
        使用 logger 美化输出 Guardian 结果，终端显示彩色，文件输出普通文本。
        result_mapping 的 logs 是 GuardianLog queryset 或 list。
        """
        sep = "=" * 80
        self.logger.info(sep)
        self.logger.info(f"[Guardian] Guarded {len(result_mapping)} objects")

        for name, result in result_mapping.items():
            success = result.get("success", False)
            if not success or result.get("logs"):
                status_text = "🔴 异常"
            else:
                status_text = "🟢 正常"
            color = self.COLORS["GREEN"] if success else self.COLORS["RED"]
            reset = self.COLORS["RESET"]

            # 输出任务组状态
            self.logger.info(f"-- {name}: {color}{status_text}{reset}")

            if success and "logs" in result:
                for log in result["logs"]:
                    action = log.action
                    spider_display = log.spider_name or (log.spider.name if log.spider else "")
                    msg = log.message or ""
                    s = "✅" if log.success else "❌"
                    action_color = self.COLORS["CYAN"]
                    success_color = self.COLORS["GREEN"] if log.success else self.COLORS["RED"]
                    self.logger.info(
                        f"   - [{action_color}{action}{reset}] {spider_display} {success_color}{s}{reset} {msg}")

            elif not success:
                error_msg = result.get("error", "Unknown error")
                self.logger.error(f"   - Error: {self.COLORS['RED']}{error_msg}{self.COLORS['RESET']}")

        self.logger.info(sep)

    def schedule(self, now):
        result_mapping = self.guard_objects()
        self.log_guard_results(result_mapping)
        signals.guard_objects_ended.send(sender=self, result=result_mapping)
