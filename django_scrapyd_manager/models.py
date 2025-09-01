# scrapyd_manager/models.py
from __future__ import annotations
from django.db import models
from django.utils.timezone import datetime
from .utils import get_md5


class Node(models.Model):
    name = models.CharField(max_length=100, verbose_name="节点名称", unique=True)
    ip = models.GenericIPAddressField(verbose_name="IP地址")
    port = models.IntegerField(default=6800, blank=True, null=True)
    ssl = models.BooleanField(default=False, verbose_name="是否启用SSL")
    description = models.CharField(max_length=500, blank=True, null=True, verbose_name="描述")
    auth = models.BooleanField(default=False, verbose_name="是否需要认证")
    username = models.CharField(max_length=255, blank=True, null=True)
    password = models.CharField(max_length=255, blank=True, null=True)
    create_time = models.DateTimeField(default=datetime.now, verbose_name="创建时间")
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


class Project(models.Model):
    node = models.ForeignKey(Node, on_delete=models.DO_NOTHING, verbose_name="节点", db_constraint=False, related_name="projects")
    name = models.CharField(max_length=255, verbose_name="项目名")
    is_deleted = models.BooleanField(default=False, verbose_name="是否已被删除")
    create_time = models.DateTimeField(default=datetime.now, verbose_name="创建时间")
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


class ProjectVersion(models.Model):
    project = models.ForeignKey(Project, on_delete=models.DO_NOTHING, verbose_name="项目", db_constraint=False, related_name="versions")
    version = models.CharField(max_length=255, verbose_name='版本')
    is_spider_synced = models.BooleanField(default=False, verbose_name="是否已同步当前版本爬虫")
    is_deleted = models.BooleanField(default=False, verbose_name="是否已被删除")
    create_time = models.DateTimeField(default=datetime.now, verbose_name="创建时间")
    update_time = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    @property
    def pretty(self):
        if self.version.isdigit():
            v = datetime.fromtimestamp(int(self.version))
        else:
            v = self.version
        return v

    @property
    def full_path(self):
        return f"{self.project.node.name}/{self.project.name}/{self.version}({self.pretty})"

    @property
    def short_path(self):
        return f"{self.version}({self.pretty})"

    class Meta:
        db_table = "scrapy_project_version"
        verbose_name = verbose_name_plural = "Scrapy Project Version"
        unique_together = (("project", "version"),)
        ordering = ["-version"]

    def __str__(self):
        return self.version


class Spider(models.Model):
    version = models.ForeignKey(ProjectVersion, on_delete=models.DO_NOTHING, verbose_name="版本", db_constraint=False, related_name="spiders")
    name = models.CharField(max_length=255, verbose_name="爬虫名称")
    create_time = models.DateTimeField(default=datetime.now, verbose_name="创建时间")
    update_time = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    def __str__(self):
        return self.name

    class Meta:
        db_table = "scrapy_spider"
        verbose_name = verbose_name_plural = "Scrapy Spider"
        unique_together = (("version", "name"),)
        ordering = ["-version", "name"]


class SpiderGroup(models.Model):
    name = models.CharField(max_length=255, verbose_name="任务组名称", unique=True)
    node = models.ForeignKey(Node, db_constraint=False, on_delete=models.DO_NOTHING, verbose_name="节点")
    project = models.ForeignKey(Project, db_constraint=False, verbose_name='项目', on_delete=models.CASCADE)
    version = models.ForeignKey(ProjectVersion, verbose_name='版本', null=True, blank=True, on_delete=models.CASCADE)
    spiders = models.JSONField(default=list, verbose_name="爬虫")
    kwargs = models.JSONField(default=dict, verbose_name="参数", null=True, blank=True)
    description = models.CharField(max_length=200, blank=True, null=True, verbose_name="任务组描述")
    create_time = models.DateTimeField(default=datetime.now, verbose_name="创建时间")
    update_time = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    @property
    def resolved_version(self):
        return self.version if self.version else self.project.latest_version

    @property
    def resolved_spiders(self):
        names = []
        for spider in self.spiders:
            names.append(spider["name"])
        spiders = Spider.objects.filter(name__in=names, version=self.resolved_version, version__project=self.project)
        return spiders

    class Meta:
        db_table = "scrapy_spider_group"
        verbose_name = verbose_name_plural = "Scrapy Spider Group"

    def __str__(self):
        return self.name


class StatusChoice(models.TextChoices):
    running = "running", "运行中"
    finished = "finished", "已结束"
    pending = "pending", "启动中"


class Job(models.Model):
    spider = models.ForeignKey(Spider, on_delete=models.DO_NOTHING, verbose_name="爬虫", db_constraint=False, related_name="jobs")
    job_id = models.CharField(max_length=255, verbose_name="任务ID")
    job_md5 = models.CharField(max_length=32, verbose_name="md5(job)", unique=True)
    start_time = models.DateTimeField(verbose_name="开始时间")
    end_time = models.DateTimeField(null=True, blank=True, verbose_name="结束时间")
    log_url = models.CharField(max_length=255, null=True, blank=True)
    items_url = models.CharField(max_length=255, null=True, blank=True)
    status = models.CharField(max_length=20, verbose_name="状态", choices=StatusChoice.choices)
    pid = models.IntegerField(null=True, blank=True, verbose_name="进程ID")
    create_time = models.DateTimeField(default=datetime.now, verbose_name="创建时间")
    update_time = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    def gen_md5(self):
        if not self.job_md5:
            start_time = self.start_time
            if isinstance(start_time, datetime):
                start_time = start_time.strftime("%Y-%m-%d %H:%M:%S.%f")
            fields = [self.spider.version.project.name, self.spider.name, self.job_id, start_time]
            self.job_md5 = get_md5('-'.join(fields))
        return self.job_md5

    def save(self, *args, **kwargs):
        self.gen_md5()
        super().save(*args, **kwargs)

    class Meta:
        db_table = "scrapy_job"
        verbose_name = verbose_name_plural = "Scrapy Job"

    def __str__(self):
        return f"{self.spider.name} - {self.job_id} ({self.status})"

