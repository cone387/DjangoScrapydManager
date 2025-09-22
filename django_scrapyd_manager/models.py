# scrapyd_manager/models.py
from __future__ import annotations
from datetime import datetime, timedelta
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone
from django.utils.deconstruct import deconstructible
from .utils import get_md5
import json
import os


class Node(models.Model):
    name = models.CharField(max_length=100, verbose_name="节点名称", unique=True)
    ip = models.GenericIPAddressField(verbose_name="IP地址")
    port = models.IntegerField(default=6800, blank=True, null=True)
    ssl = models.BooleanField(default=False, verbose_name="是否启用SSL")
    description = models.CharField(max_length=500, blank=True, null=True, verbose_name="描述")
    auth = models.BooleanField(default=False, verbose_name="是否需要认证")
    username = models.CharField(max_length=255, blank=True, null=True)
    password = models.CharField(max_length=255, blank=True, null=True)
    create_time = models.DateTimeField(default=timezone.now, verbose_name="创建时间")
    update_time = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    @classmethod
    def default_node(cls):
        for node in cls.objects.all().prefetch_related("projects"):
            if node.projects.count():
                return node
        return None

    @property
    def default_project(self):
        for project in self.projects.all():
            if project.versions.count() > 0:
                return project
        return None

    @classmethod
    def default_project_of_node(cls, node: str | int | Node):
        if isinstance(node, str):
            assert node.isdigit(), "node id非法 必须是整型"
        if not isinstance(node, Node):
            node = Node.objects.get(id=int(node))
        return node.default_project

    class Meta:
        ordering = ["-create_time"]
        db_table = "scrapyd_node"
        verbose_name = verbose_name_plural = "Scrapyd Node"

    def __str__(self):
        return self.name

    @property
    def url(self):
        host = self.ip or "localhost"
        port = self.port or 6800
        return f"{"https" if self.ssl else "http"}://{host}:{port}"


class SyncMode(models.TextChoices):
    AUTO = "auto", "自动"
    SYNC = "sync", "同步"
    NONE = "none", "无"
    

class SyncStatus(models.TextChoices):
    PENDING = "pending", "待同步"
    SUCCESS = "success", "成功"
    FAILED = "failed", "失败"


class Project(models.Model):
    node = models.ForeignKey(Node, on_delete=models.CASCADE, verbose_name="节点", db_constraint=False, related_name="projects")
    name = models.CharField(max_length=255, verbose_name="项目名")
    sync_mode = models.CharField(max_length=10, default=SyncMode.AUTO, choices=SyncMode.choices, verbose_name="同步模式")
    scrapyd_exists = models.BooleanField(default=False, verbose_name="在Scrapyd中存在")
    sync_status = models.CharField(max_length=10, default=SyncStatus.PENDING, choices=SyncStatus.choices, verbose_name="同步状态")
    create_time = models.DateTimeField(default=timezone.now, verbose_name="创建时间")
    update_time = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    @property
    def latest_version(self):
        return self.versions.order_by("-version").first()

    class Meta:
        db_table = "scrapy_project"
        verbose_name = verbose_name_plural = "Scrapy Project"
        unique_together = (("node", "name"),)

    def __str__(self):
        return self.name


@deconstructible
class EggPath:
    def __call__(self, instance, filename):
        # 始终用项目名+版本号作为文件名
        base, ext = os.path.splitext(filename)
        return f"eggs/{instance.project.name}/{instance.version}{ext}"


class ProjectVersion(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, verbose_name="项目", db_constraint=False, related_name="versions")
    version = models.CharField(max_length=255, verbose_name='版本')
    is_spider_synced = models.BooleanField(default=False, verbose_name="是否已同步当前版本爬虫")
    egg_file = models.FileField(upload_to=EggPath(), null=True, blank=True, verbose_name="Egg 文件")
    description = models.CharField(max_length=200, null=True, blank=True, verbose_name="描述")
    sync_mode = models.CharField(max_length=10, choices=SyncMode.choices, default=SyncMode.AUTO, verbose_name="同步模式")
    scrapyd_exists = models.BooleanField(default=False, verbose_name="在Scrapyd中存在")
    sync_status = models.CharField(max_length=10, default=SyncStatus.PENDING, choices=SyncStatus.choices, verbose_name="同步状态")
    create_time = models.DateTimeField(default=timezone.now, verbose_name="创建时间")
    update_time = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    @property
    def pretty(self):
        if self.version.isdigit():
            v = timezone.make_aware(datetime.fromtimestamp(int(self.version)))
        else:
            v = self.version
        return v

    @property
    def full_path(self):
        return f"{self.project.node.name}/{self.project.name}/{self.version}({self.pretty})"

    @property
    def short_path(self):
        pretty = self.pretty
        if pretty != self.version:
            return f"{self.version}({self.pretty})"
        return self.version

    class Meta:
        db_table = "scrapy_project_version"
        verbose_name = verbose_name_plural = "Scrapy Project Version"
        unique_together = (("project", "version"),)
        ordering = ["-version"]

    def __str__(self):
        return self.version


class SpiderRegistry(models.Model):
    name = models.CharField(max_length=255, verbose_name="爬虫名称", unique=True)
    description = models.CharField(max_length=200, null=True, blank=True, verbose_name="描述")
    kwargs = models.JSONField(default=dict, null=True, blank=True, verbose_name="Scrapy自定义参数(对组内所有爬虫生效)")
    settings = models.JSONField(default=dict, null=True, blank=True, verbose_name="Scrapy自定义设置(对组内所有爬虫生效)")
    create_time = models.DateTimeField(default=timezone.now, verbose_name="创建时间")
    update_time = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        db_table = "scrapy_spider_registry"
        verbose_name = verbose_name_plural = "Spider Registry"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Spider(models.Model):
    registry = models.ForeignKey(SpiderRegistry, on_delete=models.DO_NOTHING, related_name="version_spiders", verbose_name="全局爬虫", to_field="name")
    version = models.ForeignKey(ProjectVersion, on_delete=models.CASCADE, verbose_name="版本", db_constraint=False, related_name="spiders")
    name = models.CharField(max_length=255, verbose_name="爬虫名称")
    kwargs = models.JSONField(default=dict, null=True, blank=True, verbose_name="Scrapy自定义参数(对组内所有爬虫生效)")
    settings = models.JSONField(default=dict, null=True, blank=True, verbose_name="Scrapy自定义设置(对组内所有爬虫生效)")
    create_time = models.DateTimeField(default=timezone.now, verbose_name="创建时间")
    update_time = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    @property
    def fp(self):
        kwargs = self.kwargs.copy()
        for k, v in list(kwargs.items()):
            if k.startswith("__"):
                kwargs.pop(k)
        obj = {
            "version": self.version.version,
            "name": self.name,
            "kwargs": kwargs,
            "settings": self.settings,
        }
        s = json.dumps(obj, separators=(',', ':'), sort_keys=True)
        return get_md5(s)[:12]

    @property
    def job_id(self):
        group_code = self.kwargs.get("__group__") or "server"
        return f"{group_code}:{self.fp}:{self.version.version}:{timezone.now().strftime('%Y%m%d_%H%M%S')}"

    def __str__(self):
        return self.name

    class Meta:
        db_table = "scrapy_spider"
        verbose_name = verbose_name_plural = "Scrapy Spider"
        unique_together = (("version", "name"),)
        ordering = ["-version", "name"]


class SpiderGroup(models.Model):
    name = models.CharField(max_length=255, verbose_name="任务组名称", unique=True)
    code = models.CharField(max_length=100, null=True, blank=True, verbose_name="代号", validators=[
            RegexValidator(
                regex=r'^[a-zA-Z_-]*$',
                message='代号只能包含英文字母',
                code='invalid_code'
            )
        ])
    node = models.ForeignKey(Node, db_constraint=False, on_delete=models.DO_NOTHING, verbose_name="节点")
    project = models.ForeignKey(Project, db_constraint=False, verbose_name='项目', on_delete=models.CASCADE)
    version = models.ForeignKey(ProjectVersion, verbose_name='版本', null=True, blank=True, on_delete=models.CASCADE)
    spiders = models.ManyToManyField(SpiderRegistry, db_constraint=False, related_name="spiders", verbose_name="爬虫")
    kwargs = models.JSONField(default=dict, null=True, blank=True, verbose_name="Scrapy自定义参数kwargs(对组内所有爬虫生效)")
    settings = models.JSONField(default=dict, null=True, blank=True, verbose_name="Scrapy自定义设置settings(对组内所有爬虫生效)")
    description = models.CharField(max_length=200, blank=True, null=True, verbose_name="任务组描述")
    create_time = models.DateTimeField(default=timezone.now, verbose_name="创建时间")
    update_time = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    @property
    def resolved_version(self):
        return self.version if self.version else self.project.latest_version

    @property
    def resolved_spiders(self):
        version = self.resolved_version
        registry_names = self.spiders.values_list("name", flat=True)
        spiders = Spider.objects.filter(
            version=version,
            version__project=self.project,
            name__in=registry_names
        )
        for spider in spiders:
            spider.kwargs["__group__"] = self.code
            spider.kwargs.update(self.kwargs)
            spider.settings.update(self.settings)
        return spiders

    class Meta:
        db_table = "scrapy_spider_group"
        verbose_name = verbose_name_plural = "Scrapy Spider Group"

    def __str__(self):
        return f"{self.node.name}/{self.project.name}/{self.name}"


class JobStatus(models.TextChoices):
    RUNNING = "running", "运行中"
    FINISHED = "finished", "已结束"
    PENDING = "pending", "启动中"


class Job(models.Model):
    node = models.ForeignKey(Node, on_delete=models.DO_NOTHING, verbose_name="节点", db_constraint=False, related_name="jobs")
    project = models.ForeignKey(Project, on_delete=models.DO_NOTHING, verbose_name="项目", db_constraint=False, related_name="jobs")
    spider = models.ForeignKey(SpiderRegistry, on_delete=models.DO_NOTHING, verbose_name="爬虫", db_constraint=False, related_name="jobs")
    version = models.CharField(max_length=200, verbose_name="版本", null=True, blank=True)
    job_id = models.CharField(max_length=255, verbose_name="任务ID")
    job_md5 = models.CharField(max_length=32, verbose_name="md5(job)", unique=True)
    start_time = models.DateTimeField(verbose_name="开始时间")
    end_time = models.DateTimeField(null=True, blank=True, verbose_name="结束时间")
    log_url = models.CharField(max_length=255, null=True, blank=True)
    items_url = models.CharField(max_length=255, null=True, blank=True)
    status = models.CharField(max_length=20, verbose_name="状态", choices=JobStatus.choices)
    pid = models.IntegerField(null=True, blank=True, verbose_name="进程ID")
    create_time = models.DateTimeField(default=timezone.now, verbose_name="创建时间")
    update_time = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    def gen_md5(self):
        if not self.job_md5:
            start_time = self.start_time
            if isinstance(start_time, datetime):
                start_time = start_time.strftime("%Y-%m-%d %H:%M:%S.%f")
            fields = [self.project.name, self.spider.name, self.job_id, start_time]
            self.job_md5 = get_md5('-'.join(fields))
        return self.job_md5

    def save(self, *args, **kwargs):
        self.gen_md5()
        super().save(*args, **kwargs)

    @property
    def resolved_version(self):
        if not self.version:
            job_start_timestamp = int(self.start_time.timestamp())
            for version in self.project.versions.order_by("-version").all():
                if version.version.isdigit():
                    timestamp = int(version.version)
                    if timestamp < job_start_timestamp:
                        self.version = version
        return self.version

    class Meta:
        db_table = "scrapy_job"
        verbose_name = verbose_name_plural = "Scrapy Job"

    def __str__(self):
        return self.job_id


class JobInfoLog(models.Model):
    job = models.ForeignKey(Job, on_delete=models.DO_NOTHING, verbose_name="Job", db_constraint=False, related_name="logs")
    info = models.JSONField(null=True, blank=True, verbose_name="详情")
    create_time = models.DateTimeField(default=timezone.now, verbose_name="创建时间")
    update_time = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        db_table = "scrapy_job_info_log"
        verbose_name = verbose_name_plural = "Scrapy Job Info"

    def __str__(self):
        return str(self.job)


class GuardianStrategy(models.TextChoices):
    RESTART_ALWAYS = "restart_always", "始终重启"


class Guardian(models.Model):
    spider_group = models.ForeignKey(SpiderGroup, null=True, blank=True, on_delete=models.CASCADE, verbose_name="爬虫组", db_constraint=False)
    strategy = models.CharField(max_length=20, choices=GuardianStrategy.choices, default=GuardianStrategy.RESTART_ALWAYS, verbose_name="守护策略")
    description = models.CharField(max_length=200, null=True, blank=True, verbose_name="描述")
    enable = models.BooleanField(default=True, verbose_name="启用")
    interval = models.IntegerField(default=60, verbose_name="检测间隔(秒), 修改后重启生效")
    last_check = models.DateTimeField(null=True, blank=True, verbose_name="上次检测时间")
    last_action = models.CharField(max_length=255, null=True, blank=True, verbose_name="上次操作说明")
    create_time = models.DateTimeField(default=timezone.now, verbose_name="创建时间")
    update_time = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        db_table = "scrapy_guardian"
        verbose_name = verbose_name_plural = "Scrapy Guardian"

    def __str__(self):
        return f"Guardian[{self.spider_group}]"


class GuardianAction(models.TextChoices):
    PUBLISH_VERSION = "publish_version", "发布项目"
    START_SPIDER = "start_spider", "启动爬虫"


class GuardianLog(models.Model):
    guardian = models.ForeignKey(Guardian, on_delete=models.CASCADE, verbose_name="守护任务", db_constraint=False, related_name="logs")
    node = models.ForeignKey(Node, on_delete=models.CASCADE, verbose_name="节点")
    group = models.ForeignKey(SpiderGroup, on_delete=models.DO_NOTHING, db_constraint=False, verbose_name="关联爬虫组")
    spider = models.ForeignKey(Spider, null=True, blank=True, on_delete=models.DO_NOTHING, verbose_name="爬虫")
    spider_name = models.CharField(max_length=100, null=True, blank=True, verbose_name="爬虫名")
    action = models.CharField(max_length=100, choices=GuardianAction.choices, verbose_name="执行动作")
    reason = models.CharField(max_length=200, null=True, blank=True, verbose_name="原因")
    success = models.BooleanField(default=True, verbose_name="成功")
    message = models.TextField(null=True, blank=True, verbose_name="详细日志")
    create_time = models.DateTimeField(default=timezone.now, verbose_name="创建时间")

    class Meta:
        db_table = "scrapy_guardian_log"
        verbose_name = verbose_name_plural = "Scrapy Guardian Log"
        ordering = ["-create_time"]

    def __str__(self):
        return f"[{self.create_time}] {self.guardian} {self.action} ({self.success})"


