# scrapyd_manager/admin.py
from functools import wraps

from django.contrib import admin, messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from datetime import datetime
from django.utils.html import format_html
from django.urls import path
from django.shortcuts import redirect
from django.conf import settings
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from . import models
from . import scrapyd_api
from . import forms
import logging


admin_project_name = "django_scrapyd_manager"
admin_prefix = settings.FORCE_SCRIPT_NAME if settings.FORCE_SCRIPT_NAME else ''
admin_prefix = f"{admin_prefix}/admin"


logger = logging.getLogger("django_scrapyd_manager")


class ScrapydSyncAdminMixin:
    """
    通用 Mixin：给任何 ModelAdmin 自动加 scrapyd 同步
    - 同步频率由 @django_ttl_cache 控制
    """

    def sync_with_scrapyd(self):
        scrapyd_api.sync_nodes()

    def _wrap_view(self, view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            try:
                # 每次进入 admin 页面时都会触发
                self.sync_with_scrapyd()
            except Exception as e:
                logger.error(f"[scrapyd sync error]: {e}")
            return view(request, *args, **kwargs)
        return wrapper

    def get_urls(self):
        urls = super().get_urls()
        for url in urls:
            url.callback = self._wrap_view(url.callback)  # 直接替换 view
        return urls


@receiver(post_delete, sender=models.Project)
def on_project_deleted(sender, instance: models.Project, **kwargs):
    if instance.sync_mode in (models.SyncMode.AUTO, models.SyncMode.SYNC):
        if instance.sync_status == models.SyncStatus.SUCCESS:
            try:
                scrapyd_api.delete_project(instance)
            except Exception as e:
                logger.exception(e)
                instance.sync_status = models.SyncStatus.FAILED
                instance.save()


@receiver(post_delete, sender=models.ProjectVersion)
def on_project_version_deleted(sender, instance: models.ProjectVersion, **kwargs):
    if instance.sync_mode in (models.SyncMode.AUTO, models.SyncMode.SYNC):
        if instance.sync_status == models.SyncStatus.SUCCESS:
            try:
                scrapyd_api.delete_version(instance)
            except Exception as e:
                logger.exception(e)
                instance.sync_status = models.SyncStatus.FAILED
                instance.save()


@receiver(post_save, sender=models.ProjectVersion)
def on_project_version_save(sender, instance: models.ProjectVersion, created, **kwargs):
    if instance.sync_mode != models.SyncMode.NONE:
        if instance.sync_status == models.SyncStatus.PENDING:
            try:
                scrapyd_api.add_version(instance)
                instance.sync_status = models.SyncStatus.SUCCESS
            except Exception as e:
                instance.sync_status = models.SyncStatus.FAILED
                logger.exception(e)
            instance.save(update_fields=["sync_status"])
    else:
        logger.info(f"{instance.sync_mode} is {instance.sync_status}, ignored.")


class CustomFilter(admin.SimpleListFilter):

    def choices(self, changelist):
        add_facets = changelist.add_facets
        facet_counts = self.get_facet_queryset(changelist) if add_facets else None
        for i, (lookup, title) in enumerate(self.lookup_choices):
            if add_facets:
                if (count := facet_counts.get(f"{i}__c", -1)) != -1:
                    title = f"{title} ({count})"
                else:
                    title = f"{title} (-)"
            yield {
                "selected": self.value() == str(lookup),
                "query_string": changelist.get_query_string(
                    {self.parameter_name: lookup}
                ),
                "display": title,
            }

    def queryset(self, request, queryset):
        value = self.value()
        if value:
            return queryset.filter(**{self.parameter_name: value})
        return queryset


@admin.register(models.Node)
class NodeAdmin(admin.ModelAdmin):
    list_display = ("name", "linked_url", "description", "related_projects", "auth", "daemon_status", "create_time")
    readonly_fields = ("create_time", "update_time")

    def linked_url(self, obj: models.Node) -> str:
        return format_html(f"<a href='{obj.url}'>{obj.url}</a>")
    linked_url.short_description = "Scrapyd地址"

    def daemon_status(self, obj: models.Node):
        return scrapyd_api.daemon_status(obj).get("status") == "ok"
    daemon_status.short_description = "状态"
    daemon_status.boolean = True

    def related_projects(self, obj: models.Node):
        projects = []
        for project in obj.projects.all()[:5]:
            href = f"{admin_prefix}/{admin_project_name}/{models.Project._meta.model_name}/?node_id={obj.id}"
            projects.append(f"<a href='{href}'>{project.name}</a>")
        if len(projects) == 5:
            projects.append("···")
        return format_html('<span style="line-height: 1">%s</span>' % '<br>'.join(projects))
    related_projects.short_description = "项目"


class ProjectNodeFilter(CustomFilter):
    """右侧过滤：Node（节点）"""
    title = "节点"
    parameter_name = "node_id"

    def lookups(self, request, model_admin):
        return [(str(n.id), n.name) for n in models.Node.objects.all().order_by("name")]


class ProjectFilter(CustomFilter):
    """右侧过滤：Project（项目）"""
    title = "项目"
    parameter_name = "name"

    node_filter = ProjectNodeFilter

    def lookups(self, request, model_admin):
        node_id = request.GET.get(self.node_filter.parameter_name)
        if node_id:
            projects = models.Project.objects.filter(node_id=node_id).values_list("name", flat=True).distinct().order_by("name")
            return [(name, name) for name in projects]
        return []


@admin.register(models.Project)
class ProjectAdmin(ScrapydSyncAdminMixin, admin.ModelAdmin):
    list_display = ("node", "name", "latest_version", "related_versions", "scrapyd_exists", "sync_mode", "sync_status", "create_time")
    readonly_fields = ("sync_status", "create_time", "update_time")
    list_filter = (ProjectNodeFilter, )

    fields = (
        ("node", "sync_mode", "sync_status"),
        "name",
        "create_time",
        "update_time",
    )

    def has_change_permission(self, request, obj = ...):
        return False

    def delete_queryset(self, request, queryset):
        for obj in queryset:
            obj.delete()

    def latest_version(self, obj: models.Project):
        version = obj.latest_version
        if version:
            href = f"{admin_prefix}/{admin_project_name}/{models.ProjectVersion._meta.model_name}/?id={version.id}"
            return format_html(f'<a href="{href}">{version.pretty}</a>')
        return '-'
    latest_version.short_description = "最新版本"

    def related_versions(self, obj: models.Project):
        href = f"{admin_prefix}/{admin_project_name}/{models.ProjectVersion._meta.model_name}/?project_id={obj.id}"
        return format_html(f'<a href="{href}">{obj.versions.count()}</a>')
    related_versions.short_description = "版本数量"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("node")


class VersionNodeFilter(ProjectNodeFilter):
    # 使用与 Django 内置 FieldListFilter 一致的参数名，便于复用已有的默认逻辑
    parameter_name = "project__node_id"


class VersionProjectFilter(ProjectFilter):
    """右侧过滤：Project（按项目名，不含版本），受 Node 选择联动"""
    parameter_name = "project__name"
    node_filter = VersionNodeFilter


@admin.register(models.ProjectVersion)
class ProjectVersionAdmin(ScrapydSyncAdminMixin, admin.ModelAdmin):
    list_display = ("id", "linked_version", "project", "spider_count", "has_egg_file", "description", "scrapyd_exists", "sync_mode", "sync_status", "is_spider_synced", "create_time")
    readonly_fields = ("sync_status", "is_spider_synced", "create_time", "update_time")
    list_filter = (VersionNodeFilter, VersionProjectFilter)
    form = forms.ProjectVersionForm
    fields = (
        ("node", "sync_mode", "sync_status"),
        "project",
        "version",
        "description",
        "egg_file",
    )

    def has_egg_file(self, obj: models.ProjectVersion):
        if obj.egg_file and obj.egg_file.storage.exists(obj.egg_file.name):
            return True
        return False
    has_egg_file.boolean = True
    has_egg_file.short_description = "egg文件"

    def linked_version(self, obj: models.ProjectVersion):
        if obj.version.isdigit():
            version_datetime = datetime.fromtimestamp(int(obj.version))
        else:
            version_datetime = obj.version
        href = f"{admin_prefix}/{admin_project_name}/{models.Spider._meta.model_name}/?version_id={obj.id}"
        return format_html(f'<a href="{href}">{obj.version}({version_datetime})</a>')
    linked_version.admin_order_field = "version"
    linked_version.short_description = "版本"

    def spider_count(self, obj: models.ProjectVersion):
        return obj.spiders.count()
    spider_count.short_description = "爬虫数量"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("project", "project__node")

    class Media:
        js = ("admin/js/core.js", "js/spider_group_linked.js")


class SpiderNodeFilter(ProjectNodeFilter):
    # 使用与 Django 内置 FieldListFilter 一致的参数名，便于复用已有的默认逻辑
    parameter_name = "version__project__node_id"


class SpiderProjectFilter(ProjectFilter):
    """右侧过滤：Project（按项目名，不含版本），受 Node 选择联动"""
    parameter_name = "version__project__name"
    node_filter = SpiderNodeFilter


class SpiderProjectVersionFilter(CustomFilter):
    title = "版本"
    parameter_name = "version"

    def lookups(self, request, model_admin):
        node_id = request.GET.get(SpiderNodeFilter.parameter_name)
        project_name = request.GET.get(SpiderProjectFilter.parameter_name)
        if not (project_name and node_id):
            return []
        # 获取当前节点下的所有项目版本
        versions = models.ProjectVersion.objects.filter(
            project__node_id=node_id,
            project__name=project_name,
        )
        return [(v.id, v.short_path) for v in versions]


@admin.register(models.SpiderRegistry)
class SpiderRegistryAdmin(ScrapydSyncAdminMixin, admin.ModelAdmin):
    list_display = ("name", "formatted_kwargs", "formatted_settings", "create_time")
    readonly_fields = ("name", "create_time", "update_time")

    def formatted_kwargs(self, obj: models.SpiderRegistry):
        args = []
        for key, value in obj.kwargs.items():
            args.append(f"{key} = {value}")
        if len(args) == 5:
            args.append("···")
        return format_html('<span style="line-height: 1">%s</span>' % '<br>'.join(args))
    formatted_kwargs.short_description = "参数(kwargs)"

    def formatted_settings(self, obj: models.SpiderRegistry):
        args = []
        for key, value in obj.settings.items():
            args.append(f"{key} = {value}")
        if len(args) == 5:
            args.append("···")
        return format_html('<span style="line-height: 1">%s</span>' % '<br>'.join(args))
    formatted_settings.short_description = "设置(setting)"

    def has_delete_permission(self, request, obj: models.Spider = None):
        return False


@admin.register(models.Spider)
class SpiderAdmin(ScrapydSyncAdminMixin, admin.ModelAdmin):
    list_display = ("name", "project_name", "project_node_name", "formatted_kwargs", "formatted_settings", "start_spider", "create_time")
    readonly_fields = ("version", "name", "create_time", "update_time")
    list_filter = (SpiderNodeFilter, SpiderProjectFilter, SpiderProjectVersionFilter)
    actions = ["start_spiders"]

    def formatted_kwargs(self, obj: models.SpiderGroup):
        args = []
        for key, value in obj.kwargs.items():
            args.append(f"{key} = {value}")
        if len(args) == 5:
            args.append("···")
        return format_html('<span style="line-height: 1">%s</span>' % '<br>'.join(args))
    formatted_kwargs.short_description = "参数(kwargs)"

    def formatted_settings(self, obj: models.SpiderGroup):
        args = []
        for key, value in obj.settings.items():
            args.append(f"{key} = {value}")
        if len(args) == 5:
            args.append("···")
        return format_html('<span style="line-height: 1">%s</span>' % '<br>'.join(args))
    formatted_settings.short_description = "设置(setting)"

    def has_delete_permission(self, request, obj: models.Spider = None):
        if obj is None:
            return False
        return obj.version.sync_mode != models.SyncMode.NONE

    def get_urls(self):

        urls = super().get_urls()
        custom_urls = [
            path(
                "<path:spider_id>/start/",
                self.admin_site.admin_view(self.start_spider_view),
                name="scrapy_spider_start",
            ),
        ]
        return custom_urls + urls

    def start_spider_view(self, request, spider_id):
        spider = get_object_or_404(models.Spider, pk=spider_id)
        try:
            job_id = scrapyd_api.start_spider(spider)
            self.message_user(request, f"成功启动爬虫 {spider.name} (job_id={job_id})", level=messages.SUCCESS)
        except Exception as e:
            self.message_user(request, f"启动失败: {e}", level=messages.ERROR)
        from django.shortcuts import redirect
        return redirect(request.META.get("HTTP_REFERER", f"{admin_prefix}/{admin_project_name}/spider/"))

    def project_name(self, obj: models.Spider):
        return obj.version.project.name
    project_name.admin_order_field = "name"
    project_name.short_description = "项目名称"

    def project_node_name(self, obj: models.Spider):
        return obj.version.project.node.name
    project_node_name.admin_order_field = "project__node__name"
    project_node_name.short_description = "节点名称"

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("version", "version__project", "version__project__node")

    def start_spider(self, obj: models.Spider):
        return format_html(
            '<a class="button" href="{}">启动</a>',
            f"{admin_prefix}/{admin_project_name}/{models.Spider._meta.model_name}/{obj.id}/start/"
        )
    start_spider.short_description = "运行"

    def start_spiders(self, request, queryset):
        """启动选中的爬虫"""
        if not queryset:
            messages.error(request, "请选择要启动的爬虫")
            return
        for spider in queryset:
            try:
                job_id = scrapyd_api.start_spider(spider)
                messages.success(request, f"成功启动爬虫 {spider.name} (job_id={job_id})")
            except Exception as e:
                messages.error(request, f"启动爬虫 {spider.name} 失败: {str(e)}")
    start_spiders.short_description = "启动选中的爬虫"


@admin.register(models.SpiderGroup)
class SpiderGroupAdmin(ScrapydSyncAdminMixin, admin.ModelAdmin):
    list_display = ("name", "node", "project", "related_spiders", "formatted_kwargs", "formatted_settings", "formatted_version", "start_spider_group", "create_time")
    readonly_fields = ("create_time", "update_time")
    filter_horizontal = ("spiders", )
    form = forms.SpiderGroupForm
    actions = ["start_group_spiders"]
    list_filter = ("node",)
    fields = (
        "name",
        ("node", "project"),
        "version",
        "spiders",
        "description",
        "kwargs",
        "settings",
        "create_time",
        "update_time",
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("project", "node", "version", "version__project", "version__project__node")

    def formatted_version(self, obj: models.SpiderGroup):
        if obj.version:
            return obj.version
        latest = obj.project.latest_version
        if latest:
            return f"自动最新版本[{latest.short_path}]"
        return "自动最新版本[暂无可用版本]"

    formatted_version.short_description = "版本"

    def formatted_kwargs(self, obj: models.SpiderGroup):
        args = []
        for key, value in obj.kwargs.items():
            args.append(f"{key} = {value}")
        if len(args) == 5:
            args.append("···")
        return format_html('<span style="line-height: 1">%s</span>' % '<br>'.join(args))
    formatted_kwargs.short_description = "参数(kwargs)"

    def formatted_settings(self, obj: models.SpiderGroup):
        args = []
        for key, value in obj.settings.items():
            args.append(f"{key} = {value}")
        if len(args) == 5:
            args.append("···")
        return format_html('<span style="line-height: 1">%s</span>' % '<br>'.join(args))
    formatted_settings.short_description = "设置(setting)"

    def related_spiders(self, obj: models.SpiderGroup):
        spiders = []
        for spider in obj.resolved_spiders:
            href = f"{admin_prefix}/{admin_project_name}/{models.Spider._meta.model_name}/?id={spider.id}"
            spiders.append(f"<a href='{href}'>{spider.name}</a>")
        if len(spiders) == 5:
            spiders.append("···")
        return format_html('<span style="line-height: 1">%s</span>' % '<br>'.join(spiders))
    related_spiders.short_description = "爬虫"

    def start_spider_group(self, obj: models.SpiderGroup):
        return format_html(
            '<a class="button" href="{}">启动</a>',
            f"{admin_prefix}/{admin_project_name}/{models.SpiderGroup._meta.model_name}/{obj.id}/start/"
        )
    start_spider_group.short_description = "运行"

    def start_group_spiders(self, request, queryset):
        """启动选中的爬虫组（组内所有爬虫）"""
        if not queryset:
            messages.error(request, "请选择要启动的爬虫组")
            return
        for group in queryset:
            spiders = group.resolved_spiders
            if not spiders:
                messages.warning(request, f"爬虫组 {group.name} 内没有爬虫")
                continue
            for spider in spiders:
                spider.kwargs["__group__"] = group.name
                spider.kwargs.update(group.kwargs)
                spider.settings.update(group.settings)
                try:
                    job_id = scrapyd_api.start_spider(spider)
                    messages.success(request, f"组 {group.name} -> 启动爬虫 {spider.name} (job_id={job_id})")
                except Exception as e:
                    messages.error(request, f"组 {group.name} -> 启动爬虫 {spider.name} 失败: {str(e)}")
                    break

    start_group_spiders.short_description = "启动选中的爬虫组"

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "<path:group_id>/start/",
                self.admin_site.admin_view(self.start_group_view),
                name="scrapy_spidergroup_start",
            ),
            path(
                "api/node/<int:node_id>/projects/",
                self.admin_site.admin_view(self.get_projects),
                name="node_projects",
            ),
            path(
                "api/project/<int:project_id>/versions/",
                self.admin_site.admin_view(self.get_versions),
                name="project_versions",
            ),
            path(
                "api/version/spiders/",
                self.admin_site.admin_view(self.get_spiders),
                name="version_spiders",
            ),
        ]
        return custom_urls + urls

    @staticmethod
    def get_projects(request, node_id):
        projects = models.Project.objects.filter(node_id=node_id, scrapyd_exists=True)
        data = [{"id": p.id, "text": p.name} for p in projects]
        return JsonResponse(data, safe=False)

    @staticmethod
    def get_versions(request, project_id):
        if project_id:
            versions = models.ProjectVersion.objects.filter(project_id=project_id, scrapyd_exists=True).order_by("-create_time")
        else:
            node_id = request.GET.get("node_id")
            versions = models.ProjectVersion.objects.filter(project__node_id=node_id).order_by("-create_time")
        data = [{"id": v.id, "text": f"{v.version} ({v.pretty})"} for v in versions]
        return JsonResponse(data, safe=False)

    @staticmethod
    def get_spiders(request):
        version_id = request.GET.get("version_id")
        if version_id:
            spiders = models.Spider.objects.filter(version_id=version_id)
        else:
            node_id = request.GET.get("node_id")
            project_id = request.GET.get("project_id")
            spiders = models.Spider.objects.filter(version__project_id=project_id, version__project__node_id=node_id)
        data = [{"id": s.id, "text": s.name} for s in spiders]
        return JsonResponse(data, safe=False)

    def start_group_view(self, request, group_id):
        group = get_object_or_404(models.SpiderGroup, pk=group_id)
        spiders = group.resolved_spiders
        if not spiders:
            self.message_user(request, f"组 {group.name} 内没有爬虫", level=messages.WARNING)
        for spider in spiders:
            spider.kwargs["__group__"] = group.name
            spider.kwargs.update(group.kwargs)
            spider.settings.update(group.settings)
            try:
                job_id = scrapyd_api.start_spider(spider)
                self.message_user(request, f"组 {group.name} -> 启动爬虫 {spider} (job_id={job_id})",
                                  level=messages.SUCCESS)
            except Exception as e:
                self.message_user(request, f"组 {group.name} -> 启动爬虫 {spider.name} 失败: {e}",
                                  level=messages.ERROR)
        return redirect(request.META.get("HTTP_REFERER", f"{admin_prefix}/{admin_project_name}/{models.SpiderGroup._meta.model_name}/"))

    class Media:
        js = ("admin/js/core.js", "js/spider_group_linked.js")


class JobNodeFilter(ProjectNodeFilter):
    parameter_name = "node_id"


class JobProjectFilter(ProjectFilter):
    parameter_name = "project__name"
    node_filter = JobNodeFilter


class JobStatusFilter(CustomFilter):
    parameter_name = "status"
    title = "状态"

    def value(self):
        value = super().value()
        if not value:
            value = "running"
        return value

    def lookups(self, request, model_admin):
        filters = [
            (x, models.JobStatus[x].label) for x in models.Job.objects.values_list("status", flat=True).distinct()
        ]
        filters.sort()
        return filters


@admin.register(models.Job)
class JobAdmin(ScrapydSyncAdminMixin, admin.ModelAdmin):
    list_display = (
        "job_id", "job_spider", "job_project_version", "start_time", "end_time", "status", "pid", "job_sample_records", "job_info", "stop_job",
    )
    readonly_fields = ("create_time", "update_time", "start_time", "end_time", "pid", "log_url", "items_url", "spider", "status")
    list_filter = (JobStatusFilter, JobNodeFilter, JobProjectFilter)
    actions = ["stop_jobs"]
    ordering = ("-status", "-start_time")

    def sync_with_scrapyd(self):
        scrapyd_api.sync_nodes(with_jobs=True)

    def job_node(self, obj: models.Job):
        return obj.node.name
    job_node.admin_order_field = "node_id"
    job_node.short_description = "节点名称"

    def job_project(self, obj: models.Job):
        return obj.project.name
    job_project.admin_order_field = "project"
    job_project.short_description = "项目名称"

    def job_project_version(self, obj: models.Job):
        return obj.resolved_version
    job_project_version.admin_order_field = "project"
    job_project_version.short_description = "版本(根据job时间匹配)"

    def job_spider(self, obj: models.Job):
        return obj.spider.name
    job_spider.admin_order_field = "spider__name"
    job_spider.short_description = "爬虫名称"

    def job_sample_records(self, obj: models.Job):
        href = f"{admin_prefix}/{admin_project_name}/{models.JobInfoLog._meta.model_name}/?job={obj.id}"
        return format_html(f'<a class="button" href="{href}">采样记录({obj.logs.count()})</a>',)
    job_sample_records.short_description = "日志记录"

    def job_info(self, obj: models.Job):
        href = f"{admin_prefix}/{admin_project_name}/{models.JobInfoLog._meta.model_name}/?job={obj.id}"
        return format_html(f'<a class="button" href="{href}">Job最新状态</a>',)
    job_info.short_description = "Job最新状态"

    def get_list_display(self, request):
        if request.method != "GET":
            return self.list_display
        if request.GET.get("status") != "finished":
            list_display = list(self.list_display)
            list_display.remove("end_time")
            return list_display
        return self.list_display

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "<path:job_id>/stop/",
                self.admin_site.admin_view(self.stop_job_view),
                name="scrapy_job_stop",
            ),
            path(
                "<path:job_id>/info/",
                self.admin_site.admin_view(self.sync_job_info_view),
                name="scrapy_job_stop",
            ),
        ]
        return custom_urls + urls

    def sync_job_info_view(self, request, job_id):
        job = get_object_or_404(models.Job, pk=job_id)
        try:
            info = scrapyd_api.get_job_info(job)
            models.JobInfoLog.objects.create(job=job, info=info)
            self.message_user(request, f"Job日志同步成功 {job.job_id} ({job.spider.name})", level=messages.SUCCESS)
        except Exception as e:
            self.message_user(request, f"Job日志同步失败: {e}", level=messages.ERROR)
        return redirect(request.META.get("HTTP_REFERER", f"{admin_prefix}/{admin_project_name}/{models.Job._meta.model_name}/"))

    def stop_job_view(self, request, job_id):
        job = get_object_or_404(models.Job, pk=job_id)
        try:
            scrapyd_api.stop_job(job)
            self.message_user(request, f"成功停止任务 {job.job_id} ({job.spider.name})", level=messages.SUCCESS)
        except Exception as e:
            self.message_user(request, f"停止任务失败: {e}", level=messages.ERROR)
        return redirect(request.META.get("HTTP_REFERER", f"{admin_prefix}/{admin_project_name}/{models.Job._meta.model_name}/"))

    def stop_job(self, obj: models.Job):
        if obj.status == "running":  # 可选: 只在运行中显示按钮
            return format_html(
                '<a class="button" href="{}">停止</a>',
                f"{admin_prefix}/{admin_project_name}/{models.Job._meta.model_name}/{obj.id}/stop/"
            )
        return "-"
    stop_job.short_description = "操作"

    def stop_jobs(self, request, queryset):
        """停止选中的爬虫任务"""
        if not queryset:
            messages.error(request, "请选择要停止的任务")
            return
        for job in queryset:
            try:
                scrapyd_api.stop_job(job)
                messages.success(request, f"成功停止任务 {job.job_id} ({job.spider.name})")
            except Exception as e:
                messages.error(request, f"停止任务 {job.job_id} 失败: {str(e)}")
    stop_jobs.short_description = "停止选中的爬虫任务"

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("spider", "node", "project")

    def get_object(self, request, object_id, from_field = ...):
        return get_object_or_404(models.Job, pk=object_id)


@admin.register(models.JobInfoLog)
class JobInfoLogAdmin(admin.ModelAdmin):
    list_display = (
        "job", "job_node", "job_project",
    )

    list_filter = ("job__node", "job__project", "job__spider")
    ordering = ("-job_id", "-create_time")

    def has_change_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request):
        return False

    def job_id(self, obj: models.JobInfoLog):
        return obj.job.job_id
    job_id.short_description = "JobId"

    def job_node(self, obj: models.JobInfoLog):
        return obj.job.node
    job_node.admin_order_field = "node_id"
    job_node.short_description = "节点名称"

    def job_project(self, obj: models.JobInfoLog):
        return obj.job.project
    job_project.admin_order_field = "project"
    job_project.short_description = "项目名称"

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("job")

# @admin.register(models.SpiderGuardian)
# class SpiderGuardianAdmin(admin.ModelAdmin):
#     list_display = (
#         "job_id", "job_project_version", "job_spider", "start_time", "end_time", "status", "pid", "stop_job",
#     )
#     readonly_fields = ("create_time", "update_time", "start_time", "end_time", "pid", "log_url", "items_url", "spider", "status")
#     list_filter = (JobStatusFilter, JobNodeFilter, JobProjectFilter)
#     actions = ["start_jobs", "stop_jobs"]
#     ordering = ("-status", "-start_time")
#
#     def job_node(self, obj):
#         return obj.spider.version.project.node.name
#     job_node.admin_order_field = "spider__version__project__node__name"
#     job_node.short_description = "节点名称"
#
#     def get_queryset(self, request):
#         return super().get_queryset(request).prefetch_related("spider", "version", "version", "node")
#
#
# @admin.register(models.SpiderGuardianLog)
# class SpiderGuardianLogAdmin(admin.ModelAdmin):
#     list_display = (
#         "guardian", "node", "spider", "action", "result", "create_time",
#     )
#     ordering = ("-create_time", )
#
#     def has_change_permission(self, request, obj = ...):
#         return False