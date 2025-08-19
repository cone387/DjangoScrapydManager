# scrapyd_manager/models.py
from django.db import models
from django.utils.timezone import datetime


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
        return f"http://{host}:{port}"


class Project(models.Model):
    node = models.ForeignKey(Node, on_delete=models.DO_NOTHING, verbose_name="节点", db_constraint=False)
    name = models.CharField(max_length=255, default=None)
    version = models.CharField(max_length=255, default=None)
    create_time = models.DateTimeField(default=datetime.now, verbose_name="创建时间")
    update_time = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        db_table = "scrapy_project"
        verbose_name = verbose_name_plural = "Scrapy Project"
        unique_together = (("node", "name", "version"),)

    def __str__(self):
        return self.name


class Spider(models.Model):
    project = models.ForeignKey(Project, on_delete=models.DO_NOTHING, verbose_name="项目", db_constraint=False)
    name = models.CharField(max_length=255, verbose_name="爬虫名称")
    create_time = models.DateTimeField(default=datetime.now, verbose_name="创建时间")
    update_time = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    def __str__(self):
        return f"{self.name}@{self.project.name}"

    class Meta:
        db_table = "scrapy_spider"
        verbose_name = verbose_name_plural = "Scrapy Spider"
        unique_together = (("project", "name"),)


class SpiderGroup(models.Model):
    spiders = models.ManyToManyField(Spider, verbose_name="爬虫", db_constraint=False)
    name = models.CharField(max_length=255, verbose_name="任务组名称", unique=True)
    description = models.TextField(blank=True, null=True, verbose_name="任务组描述")
    create_time = models.DateTimeField(default=datetime.now, verbose_name="创建时间")
    update_time = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        db_table = "scrapy_spider_group"
        verbose_name = verbose_name_plural = "Scrapy Spider Group"

    def __str__(self):
        return self.name


class Job(models.Model):
    spider = models.ForeignKey(Spider, on_delete=models.DO_NOTHING, verbose_name="爬虫", db_constraint=False)
    job_id = models.CharField(max_length=255, verbose_name="任务ID")
    start_time = models.DateTimeField(verbose_name="开始时间")
    end_time = models.DateTimeField(null=True, blank=True, verbose_name="结束时间")
    log_url = models.CharField(max_length=255, null=True, blank=True)
    items_url = models.CharField(max_length=255, null=True, blank=True)
    status = models.CharField(max_length=20, verbose_name="状态")
    pid = models.IntegerField(null=True, blank=True, verbose_name="进程ID")
    create_time = models.DateTimeField(default=datetime.now, verbose_name="创建时间")
    update_time = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        db_table = "scrapy_job"
        verbose_name = verbose_name_plural = "Scrapy Job"

    def __str__(self):
        return f"{self.spider.name} - {self.job_id} ({self.status})"

