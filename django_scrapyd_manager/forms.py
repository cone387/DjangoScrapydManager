import time

from django import forms
from django.core.exceptions import ValidationError

from . import models
from . import scrapyd_api
from .models import Project

ALLOWED_SCRAPY_CUSTOM_SETTINGS = {
  "CONCURRENT_REQUESTS": {
    "default": 16,
    "description": "Scrapy 全局最大并发请求数"
  },
  "CONCURRENT_REQUESTS_PER_DOMAIN": {
    "default": 8,
    "description": "每个域名最大并发请求数"
  },
  "CONCURRENT_REQUESTS_PER_IP": {
    "default": 0,
    "description": "每个 IP 最大并发请求数，0 表示禁用"
  },
  "DOWNLOAD_DELAY": {
    "default": 0,
    "description": "下载延迟，单位秒"
  },
  "RANDOMIZE_DOWNLOAD_DELAY": {
    "default": True,
    "description": "是否随机化下载延迟"
  },
  "AUTOTHROTTLE_ENABLED": {
    "default": False,
    "description": "是否启用自动限速"
  },
  "AUTOTHROTTLE_START_DELAY": {
    "default": 5,
    "description": "自动限速起始延迟"
  },
  "AUTOTHROTTLE_MAX_DELAY": {
    "default": 60,
    "description": "自动限速最大延迟"
  },
  "COOKIES_ENABLED": {
    "default": True,
    "description": "是否启用 Cookies"
  },
  "COOKIES_DEBUG": {
    "default": False,
    "description": "打印 Cookies 调试信息"
  },
  "RETRY_ENABLED": {
    "default": True,
    "description": "是否启用请求重试"
  },
  "RETRY_TIMES": {
    "default": 2,
    "description": "请求最大重试次数"
  },
  "DOWNLOAD_TIMEOUT": {
    "default": 180,
    "description": "下载超时，单位秒"
  },
  "REDIRECT_ENABLED": {
    "default": True,
    "description": "是否允许 HTTP 重定向"
  },
  "USER_AGENT": {
    "default": "Scrapy/VERSION (+https://scrapy.org)",
    "description": "请求 User-Agent"
  },
  "DEFAULT_REQUEST_HEADERS": {
    "default": {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
    "description": "默认请求头"
  },
  "ITEM_PIPELINES": {
    "default": {},
    "description": "Item 管道配置，格式为 {'路径': 优先级}"
  },
  "DOWNLOADER_MIDDLEWARES": {
    "default": {},
    "description": "下载器中间件配置，格式为 {'路径': 优先级}"
  },
  "SPIDER_MIDDLEWARES": {
    "default": {},
    "description": "爬虫中间件配置，格式为 {'路径': 优先级}"
  },
  "LOG_LEVEL": {
    "default": "DEBUG",
    "description": "日志等级，可选 DEBUG/INFO/WARNING/ERROR/CRITICAL"
  },
  "LOG_ENABLED": {
    "default": True,
    "description": "是否启用日志"
  },
  "FEED_FORMAT": {
    "default": "json",
    "description": "导出文件格式，如 json/csv/xml"
  },
  "FEED_URI": {
    "default": "",
    "description": "导出文件路径或 URI"
  },
  "ROBOTSTXT_OBEY": {
    "default": True,
    "description": "是否遵守 robots.txt"
  },
  "DEPTH_LIMIT": {
    "default": 0,
    "description": "爬取深度限制，0 表示无限"
  },
  "DEPTH_PRIORITY": {
    "default": 0,
    "description": "深度优先或广度优先，>0 深度优先，<0 广度优先"
  },
  "EXTENSIONS": {
    "default": {},
    "description": "Scrapy 扩展配置，格式为 {'路径': 优先级}"
  },
  "TELNETCONSOLE_ENABLED": {
    "default": True,
    "description": "是否启用 Telnet 控制台"
  },
  "DUPEFILTER_CLASS": {
    "default": "scrapy.dupefilters.RFPDupeFilter",
    "description": "去重过滤器类"
  },
  "SCHEDULER": {
    "default": "scrapy.core.scheduler.Scheduler",
    "description": "Scrapy 调度器类"
  }
}


class SpiderGroupForm(forms.ModelForm):
    settings = forms.JSONField(
        required=False,
        help_text="Scrapy 支持的配置项(JSON)，示例: {\"CONCURRENT_REQUESTS\": 8}"
    )

    class Meta:
        model = models.SpiderGroup
        fields = "__all__"

    def __init__(self, *args, instance: models.SpiderGroup=None, **kwargs):
        super().__init__(*args, instance=instance, **kwargs)
        if instance is not None:
            self.fields["project"].widget.choices = [(p.pk, p.name) for p in instance.node.projects.all()]
            self.fields["version"].widget.choices = [(v.pk, v.version) for v in instance.project.versions.all()]
            self.fields["project"].queryset = models.Project.objects.filter(scrapyd_exists=True)
            self.fields["version"].queryset = models.ProjectVersion.objects.filter(scrapyd_exists=True)
        try:
            latest_version = instance.project.latest_version
        except AttributeError:
            latest_version = None
        if latest_version is None:
            version_empty_label = "自动最新版本[暂无可用版本]"
        else:
            version_empty_label = f"自动最新版本[{latest_version.short_path}]"
        self.fields["version"].empty_label = version_empty_label
        self.fields["version"].label_from_instance = lambda obj: obj.short_path

    def clean_settings(self):
        data = self.cleaned_data.get("settings") or {}
        if not isinstance(data, dict):
            raise ValidationError("custom_settings 必须是一个 JSON 对象")
        invalid_keys = [k for k in data.keys() if k not in ALLOWED_SCRAPY_CUSTOM_SETTINGS]
        if invalid_keys:
            raise ValidationError(f"无效的配置项: {invalid_keys}")
        return data

    def clean(self):
        cleaned_data = super().clean()
        if self.errors:
            return cleaned_data
        node = cleaned_data["node"]
        project = cleaned_data["project"]
        version: models.ProjectVersion = cleaned_data["version"]
        if project.node != node:
            raise ValidationError(f"project node({project.node})和group node({node})不一致！")
        if version:
            if version.project != project:
                raise ValidationError(f"version所输project{version.project}与group所输project({project})不一致！")
        return cleaned_data


class ProjectVersionForm(forms.ModelForm):
    project = forms.ModelChoiceField(queryset=Project.objects, required=True)
    node = forms.ModelChoiceField(queryset=models.Node.objects, required=True)
    egg_file = forms.FileField(
        required=True,
        label="Egg 文件上传",
        help_text="可手动上传 Scrapy 打包好的 egg 文件。"
    )
    version = forms.CharField(required=False)

    def __init__(self, *args, instance=None, **kwargs):
        super().__init__( *args, instance=instance, **kwargs)
        project: forms.ModelChoiceField = self.fields["project"] # type: ignore
        if instance is None:
            try:
                default_node = models.Node.default_node()
                self.fields["node"].initial = default_node
                project.queryset = default_node.projects.filter(scrapyd_exists=True)
                project.initial = project.queryset.first()
            except models.Node.DoesNotExist:
                pass
        else:
            project.queryset = instance.project.node.projects.all()
        if self.instance.pk:
            self.instance: models.ProjectVersion
            self.fields["node"].initial = self.instance.project.node
            for name, field in self.fields.items():
                if name != "description":
                    field.disabled = True
                    field.required = False
        self.default_version = str(int(time.time()))
        self.fields["version"].help_text = self.default_version

    def clean(self):
        cleaned_data = super().clean()
        if self.errors:
            return cleaned_data
        if self.instance.pk:
            return {"description": cleaned_data["description"]}
        if not cleaned_data["version"]:
            cleaned_data["version"] = self.default_version
        node = cleaned_data.pop("node")
        if node != cleaned_data["project"].node:
            raise ValidationError("node和project所在node不一致")
        obj = models.ProjectVersion(**cleaned_data)
        ret = scrapyd_api.add_version(obj)
        status = ret.get("status")
        if status == "error":
            raise ValidationError(ret.get("message"))
        return cleaned_data

    class Meta:
        model = models.ProjectVersion
        fields = ["project", "version", "egg_file"]
