# scrapyd_manager/admin.py
from django.contrib import admin, messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from datetime import datetime
from django.utils.html import format_html
from django.urls import path
from django.shortcuts import redirect
from django.conf import settings
from . import models
from . import scrapyd_api
from . import forms


admin_project_name = "django_scrapyd_manager"
admin_prefix = settings.FORCE_SCRIPT_NAME if settings.FORCE_SCRIPT_NAME else ''
admin_prefix = f"{admin_prefix}/admin"


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
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("name", "latest_version", "related_versions", "is_deleted", "create_time")
    readonly_fields = ("create_time", "update_time")
    list_filter = (ProjectNodeFilter, )

    def has_change_permission(self, request, obj = ...):
        return False

    def has_delete_permission(self, request, obj = ...):
        return False

    def latest_version(self, obj: models.Project):
        version = obj.versions.order_by("-version").first()
        if version:
            href = f"{admin_prefix}/{admin_project_name}/{models.ProjectVersion._meta.model_name}/?id={version.id}"
            return format_html(f'<a href="{href}">{version.pretty}</a>')
        return '-'
    latest_version.short_description = "最新版本"

    def related_versions(self, obj: models.Project):
        href = f"{admin_prefix}/{admin_project_name}/{models.ProjectVersion._meta.model_name}/?project_id={obj.id}"
        return format_html(f'<a href="{href}">{obj.versions.count()}</a>')
    related_versions.short_description = "版本数量"

    def get_object(self, request, object_id, from_field = ...):
        return get_object_or_404(models.Project, pk=object_id)

    def changelist_view(self, request, extra_context=None):

        node_id = request.GET.get(ProjectNodeFilter.parameter_name)
        if node_id:
            node = get_object_or_404(models.Node, pk=node_id)
        else:
            node = models.Node.objects.order_by('name').first()
        if node:
            params = request.GET.copy()
            params[ProjectNodeFilter.parameter_name] = str(node.id)
            request.GET = params
            scrapyd_api.sync_node_projects(node)
        return super().changelist_view(request, extra_context)


class VersionNodeFilter(ProjectNodeFilter):
    # 使用与 Django 内置 FieldListFilter 一致的参数名，便于复用已有的默认逻辑
    parameter_name = "project__node_id"


class VersionProjectFilter(ProjectFilter):
    """右侧过滤：Project（按项目名，不含版本），受 Node 选择联动"""
    parameter_name = "project__name"
    node_filter = VersionNodeFilter


@admin.register(models.ProjectVersion)
class ProjectVersionAdmin(admin.ModelAdmin):
    list_display = ("id", "linked_version", "project", "spider_count", "is_spider_synced", "is_deleted", "create_time")
    readonly_fields = ("create_time", "update_time")
    list_filter = (VersionNodeFilter, VersionProjectFilter)

    def has_change_permission(self, request, obj = ...):
        return False

    def has_delete_permission(self, request, obj = ...):
        return False

    def linked_version(self, obj: models.ProjectVersion):
        if obj.version.isdigit():
            version_datetime = datetime.fromtimestamp(int(obj.version))
        else:
            version_datetime = obj.version
        href = f"{admin_prefix}/{admin_project_name}/{models.Spider._meta.model_name}/?version__version={obj.version}"
        return format_html(f'<a href="{href}">{obj.version}({version_datetime})</a>')
    linked_version.admin_order_field = "version"
    linked_version.short_description = "版本"

    def spider_count(self, obj: models.ProjectVersion):
        return obj.spiders.count()
    spider_count.short_description = "爬虫数量"

    def changelist_view(self, request, extra_context=None):
        node_id = request.GET.get(VersionProjectFilter.node_filter.parameter_name)
        project_name = request.GET.get(VersionProjectFilter.parameter_name)
        if node_id:
            node = get_object_or_404(models.Node, pk=node_id)
        else:
            node = models.Node.objects.order_by('name').first()
        if node:
            scrapyd_api.sync_node_projects(node)
            if not project_name:
                project = models.Project.objects.filter(node=node).order_by("name").first()
                if project:
                    project_name = project.name
            q = request.GET.copy()
            q[VersionProjectFilter.node_filter.parameter_name] = str(node.id)
            q[VersionProjectFilter.parameter_name] = project_name
            request.GET = q
        return super().changelist_view(request, extra_context)


class SpiderNodeFilter(ProjectNodeFilter):
    # 使用与 Django 内置 FieldListFilter 一致的参数名，便于复用已有的默认逻辑
    parameter_name = "version__project__node_id"


class SpiderProjectFilter(ProjectFilter):
    """右侧过滤：Project（按项目名，不含版本），受 Node 选择联动"""
    parameter_name = "version__project__name"
    node_filter = SpiderNodeFilter


class SpiderProjectVersionFilter(CustomFilter):
    title = "版本"
    parameter_name = "version__version"

    def lookups(self, request, model_admin):
        node_id = request.GET.get(SpiderNodeFilter.parameter_name)
        project_name = request.GET.get(SpiderProjectFilter.parameter_name)
        if not (project_name and node_id):
            return []
        # 获取当前节点下的所有项目版本
        versions = models.ProjectVersion.objects.filter(
            project__node_id=node_id,
            project__name=project_name,
        ).values_list('version', flat=True).order_by("-version")
        return [(v, f'{v}({datetime.fromtimestamp(int(v))})') for v in versions]


@admin.register(models.Spider)
class SpiderAdmin(admin.ModelAdmin):
    list_display = ("name", "project_name", "project_node_name", "start_spider", "create_time")
    readonly_fields = ("create_time", "update_time")
    list_filter = (SpiderNodeFilter, SpiderProjectFilter, SpiderProjectVersionFilter)
    actions = ["start_spiders"]

    def has_change_permission(self, request, obj = ...):
        return False

    def has_delete_permission(self, request, obj = ...):
        return False

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
        node_id = request.GET.get(SpiderNodeFilter.parameter_name)
        project_name = request.GET.get(SpiderProjectFilter.parameter_name)
        version = request.GET.get(SpiderProjectVersionFilter.parameter_name)
        if node_id and project_name and version:
            project_version = models.ProjectVersion.objects.filter(
                project__node_id=node_id, project__name=project_name, version=version,
            ).first()
            if project_version:
                scrapyd_api.sync_project_version_spiders(project_version)
                return models.Spider.objects.filter(version=project_version)
            return models.Spider.objects.none()
        return super().get_queryset(request).prefetch_related("version", "version__project")

    def get_object(self, request, object_id, from_field = ...):
        return get_object_or_404(models.Spider, pk=object_id)

    def changelist_view(self, request, extra_context=None):
        if request.GET.get("id"):
            return super().changelist_view(request, extra_context)
        node_id = request.GET.get(SpiderNodeFilter.parameter_name)
        project_name = request.GET.get(SpiderProjectFilter.parameter_name)
        version = request.GET.get(SpiderProjectVersionFilter.parameter_name)
        last_node_id = request.COOKIES.get("last_node_id")
        last_project_name = request.COOKIES.get("last_project_name")
        new_query = request.GET.copy()
        if last_node_id and last_node_id != node_id:
            new_query.pop(SpiderProjectFilter.parameter_name, None)
            new_query.pop(SpiderProjectVersionFilter.parameter_name, None)
            version = project_name = None
        elif last_project_name and last_project_name != project_name:
            new_query.pop(SpiderProjectVersionFilter.parameter_name, None)
            version = None
        if not node_id:
            node_id = models.Node.objects.order_by('name').values_list("id", flat=True).first()
        if node_id:
            new_query[SpiderNodeFilter.parameter_name] = str(node_id)
            if not project_name:
                project = models.Node.default_project_of_node(node_id)
                if project:
                    project_name = project.name if project else None
                    new_query[SpiderProjectFilter.parameter_name] = project_name
                    if not version:
                        version = project.latest_version
                        if version:
                            new_query[SpiderProjectVersionFilter.parameter_name] = version.version
        request.GET = new_query
        response = super().changelist_view(request, extra_context)
        response.set_cookie("last_node_id", node_id)
        response.set_cookie("last_project_name", project_name)
        return response

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
class SpiderGroupAdmin(admin.ModelAdmin):
    list_display = ("name", "node", "project", "related_spiders", "formatted_kwargs", "formatted_version", "start_spider_group", "create_time")
    readonly_fields = ("create_time", "update_time")
    # filter_horizontal = ("nodes",)
    form = forms.SpiderGroupForm
    actions = ["start_group_spiders"]
    list_filter = ("node",)
    fields = (
        "name",
        ("node", "project"),
        "version",
        "spiders_select",
        "description",
        "kwargs",
        "create_time",
        "update_time",
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("project", "node", "version", "version__project")

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
    formatted_kwargs.short_description = "参数"

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
        projects = models.Project.objects.filter(node_id=node_id)
        data = [{"id": p.id, "text": p.name} for p in projects]
        return JsonResponse(data, safe=False)

    @staticmethod
    def get_versions(request, project_id):
        if project_id:
            versions = models.ProjectVersion.objects.filter(project_id=project_id).order_by("-create_time")
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
    parameter_name = "spider__version__project__node_id"


class JobProjectFilter(ProjectFilter):
    parameter_name = "spider__version__project__name"
    node_filter = JobNodeFilter


class JobSpiderFilter(CustomFilter):
    """右侧过滤：Spider（爬虫）"""
    title = "爬虫"
    parameter_name = "spider_id"

    def lookups(self, request, model_admin):
        node_id = request.GET.get(JobNodeFilter.parameter_name)
        project_name = request.GET.get(JobProjectFilter.parameter_name)
        if node_id and project_name:
            spiders = models.Spider.objects.filter(project__node_id=node_id, project__name=project_name).order_by("name")
            return [(str(s.id), s.name) for s in spiders]
        return []


class JobStatusFilter(CustomFilter):
    parameter_name = "status"
    title = "状态"

    def lookups(self, request, model_admin):
        filters = [
            (x, models.StatusChoice[x].label) for x in models.Job.objects.values_list("status", flat=True).distinct()
        ]
        filters.sort()
        return filters


@admin.register(models.Job)
class JobAdmin(admin.ModelAdmin):
    list_display = (
        "job_id", "job_project_version", "job_spider", "start_time", "end_time", "status", "pid", "stop_job",
    )
    readonly_fields = ("create_time", "update_time", "start_time", "end_time", "pid", "log_url", "items_url", "spider", "status")
    list_filter = (JobStatusFilter, JobNodeFilter, JobProjectFilter)
    actions = ["start_jobs", "stop_jobs"]
    ordering = ("-status", "-start_time")

    def job_node(self, obj):
        return obj.spider.version.project.node.name
    job_node.admin_order_field = "spider__version__project__node__name"
    job_node.short_description = "节点名称"

    def job_project(self, obj):
        return obj.spider.version.project.name
    job_project.admin_order_field = "spider__version__project__name"
    job_project.short_description = "项目名称"

    def job_project_version(self, obj):
        return obj.spider.version.version
    job_project_version.admin_order_field = "spider__version__version"
    job_project_version.short_description = "项目版本"

    def job_spider(self, obj):
        return obj.spider.name
    job_spider.admin_order_field = "spider__name"
    job_spider.short_description = "爬虫名称"

    def start_jobs(self, request, queryset):
        """启动选中的 Job 对应的爬虫（重新运行）"""
        if not queryset:
            messages.error(request, "请选择要启动的任务")
            return
        for job in queryset:
            try:
                job_id = scrapyd_api.start_spider(job.spider)
                messages.success(request, f"成功重新启动任务 {job.spider.name} (job_id={job_id})")
            except Exception as e:
                messages.error(request, f"重新启动任务 {job.spider.name} 失败: {str(e)}")
    start_jobs.short_description = "重新启动选中的任务"

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "<path:job_id>/stop/",
                self.admin_site.admin_view(self.stop_job_view),
                name="scrapy_job_stop",
            ),
        ]
        return custom_urls + urls

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
        node_id = request.GET.get(JobNodeFilter.parameter_name)
        if node_id:
            node = get_object_or_404(models.Node, pk=node_id)
        else:
            return models.Job.objects.none()
        scrapyd_api.sync_jobs(node)
        return super().get_queryset(request).prefetch_related("spider", "spider__version", "spider__version__project")

    def get_object(self, request, object_id, from_field = ...):
        return get_object_or_404(models.Job, pk=object_id)

    def changelist_view(self, request, extra_context=None):
        if JobNodeFilter.parameter_name not in request.GET:
            default_node = models.Node.objects.order_by('name').first()
            if default_node:
                q = request.GET.copy()
                q[JobNodeFilter.parameter_name] = str(default_node.id)
                project_name = request.GET.get(JobProjectFilter.parameter_name)
                if project_name:
                    default_project = models.Project.objects.filter(node_id=default_node.id, name=project_name).first()
                else:
                    default_project = models.Project.objects.filter(node_id=default_node.id).first()
                if default_project:
                    q[JobProjectFilter.parameter_name] = default_project.name
                request.GET = q
        return super().changelist_view(request, extra_context)
