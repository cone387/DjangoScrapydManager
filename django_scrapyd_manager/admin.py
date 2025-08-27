# scrapyd_manager/admin.py
from django.contrib import admin, messages
from django.shortcuts import get_object_or_404
from datetime import datetime
from . import models
from . import scrapyd_api
from . import forms


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
    list_display = ("name", "ip", "port", "description", "auth", "create_time")
    readonly_fields = ("create_time", "update_time")


class ProjectNodeFilter(CustomFilter):
    """右侧过滤：Node（节点）"""
    title = "节点"
    parameter_name = "node__id"
    default_node = models.Node.objects.order_by('name').first()

    def lookups(self, request, model_admin):
        return [(str(n.id), n.name) for n in models.Node.objects.all().order_by("name")]


class ProjectFilter(CustomFilter):
    """右侧过滤：Project（项目）"""
    title = "项目"
    parameter_name = "name"

    node_filter = ProjectNodeFilter
    default_project = models.Project.objects.filter(node=node_filter.default_node).first()

    def lookups(self, request, model_admin):
        node_id = request.GET.get(self.node_filter.parameter_name)
        if node_id:
            projects = models.Project.objects.filter(node_id=node_id).values_list("name", flat=True).distinct().order_by("name")
            return [(name, name) for name in projects]
        return []


@admin.register(models.Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("name", "version", "create_time")
    readonly_fields = ("create_time", "update_time")
    list_filter = (ProjectNodeFilter, ProjectFilter)

    def get_queryset(self, request):
        node_id = request.GET.get("node__id")
        if node_id:
            node = get_object_or_404(models.Node, pk=node_id)
        else:
            return models.Project.objects.none()
        scrapyd_api.list_node_projects(node)
        return super().get_queryset(request)

    def get_object(self, request, object_id, from_field = ...):
        return get_object_or_404(models.Project, pk=object_id)

    def changelist_view(self, request, extra_context=None):
        # 如果 GET 中没有 node__id，则填充默认值
        if ProjectNodeFilter.parameter_name not in request.GET:
            default_node = models.Node.objects.order_by('name').first()
            if default_node:
                q = request.GET.copy()
                q[ProjectNodeFilter.parameter_name] = str(default_node.id)
                project_name = request.GET.get(ProjectFilter.parameter_name)
                if not project_name:
                    project = models.Project.objects.order_by('name').first()
                    if project:
                        project_name = project.name
                        q[ProjectFilter.parameter_name] = project_name
                request.GET = q
        return super().changelist_view(request, extra_context)



class SpiderNodeFilter(ProjectNodeFilter):
    # 使用与 Django 内置 FieldListFilter 一致的参数名，便于复用已有的默认逻辑
    parameter_name = "project__node_id"


class SpiderProjectFilter(ProjectFilter):
    """右侧过滤：Project（按项目名，不含版本），受 Node 选择联动"""
    parameter_name = "project__name"
    node_filter = SpiderNodeFilter


class SpiderProjectVersionFilter(CustomFilter):
    title = "版本"
    parameter_name = "project__version"

    def lookups(self, request, model_admin):
        node_id = request.GET.get(SpiderNodeFilter.parameter_name)
        if not node_id:
            return []
        project_name = request.GET.get(SpiderProjectFilter.parameter_name)
        if not project_name:
            return []
        # 获取当前节点下的所有项目版本
        versions = models.Project.objects.filter(node_id=node_id, name=project_name).values_list('version', flat=True).order_by("-version").distinct()
        return [(v, f'{v}({datetime.fromtimestamp(int(v))})') for v in versions]


@admin.register(models.Spider)
class SpiderAdmin(admin.ModelAdmin):
    list_display = ("name", "project_name", "project_node_name", "create_time")
    readonly_fields = ("create_time", "update_time")
    list_filter = (SpiderNodeFilter, SpiderProjectFilter, SpiderProjectVersionFilter)
    actions = ["start_spiders"]

    def project_name(self, obj):
        return obj.project.name
    project_name.admin_order_field = "name"
    project_name.short_description = "项目名称"

    def project_node_name(self, obj):
        return obj.project.node.name
    project_node_name.admin_order_field = "project__node__name"
    project_node_name.short_description = "节点名称"

    def get_queryset(self, request):
        node_id = request.GET.get(SpiderNodeFilter.parameter_name)
        project_name = request.GET.get(SpiderProjectFilter.parameter_name)
        version = request.GET.get(SpiderProjectVersionFilter.parameter_name)
        if node_id and project_name and version:
            project = models.Project.objects.filter(node_id=node_id, name=project_name, version=version).first()
            if project:
                scrapyd_api.list_project_spiders(project)
        return super().get_queryset(request)

    def get_object(self, request, object_id, from_field = ...):
        return get_object_or_404(models.Spider, pk=object_id)

    def changelist_view(self, request, extra_context=None):
        if SpiderNodeFilter.parameter_name not in request.GET:
            default_node = models.Node.objects.order_by('name').first()
            if default_node:
                q = request.GET.copy()
                q[SpiderNodeFilter.parameter_name] = str(default_node.id)
                project_name = request.GET.get(SpiderProjectFilter.parameter_name)
                if project_name:
                    projects = models.Project.objects.filter(node_id=default_node.id, name=project_name)
                else:
                    project = models.Project.objects.filter(node_id=default_node.id).first()
                    if project:
                        projects = models.Project.objects.filter(name=project.name)
                    else:
                        projects = models.Project.objects.none()
                version = request.GET.get(SpiderProjectVersionFilter.parameter_name)
                if version:
                    default_project = projects.filter(version=version).first()
                else:
                    default_project = projects.order_by('-version').first()
                if default_project:
                    q[SpiderProjectFilter.parameter_name] = default_project.name
                    q[SpiderProjectVersionFilter.parameter_name] = default_project.version
                request.GET = q
        return super().changelist_view(request, extra_context)

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
    list_display = ("name", "description", "create_time")
    readonly_fields = ("create_time", "update_time")
    # filter_horizontal = ("spiders",)
    form = forms.SpiderGroupForm
    actions = ["start_group_spiders"]
    fieldsets = (
        ("基础信息", {
            "fields": (("name", "version_select", ), )
        }),
        ("爬虫配置", {
            "fields": ("spiders_select", "description", "kwargs"),
            "description": "选择关联的爬虫，并可设置额外参数"
        }),
        ("时间信息", {
            "fields": ("create_time", "update_time"),
            "classes": ("collapse",),
        }),
    )

    def start_group_spiders(self, request, queryset):
        """启动选中的爬虫组（组内所有爬虫）"""
        if not queryset:
            messages.error(request, "请选择要启动的爬虫组")
            return
        for group in queryset:
            spiders = group.spiders.all()
            if not spiders:
                messages.warning(request, f"爬虫组 {group.name} 内没有爬虫")
                continue
            for spider in spiders:
                try:
                    job_id = scrapyd_api.start_spider(spider)
                    messages.success(request, f"组 {group.name} -> 启动爬虫 {spider.name} (job_id={job_id})")
                except Exception as e:
                    messages.error(request, f"组 {group.name} -> 启动爬虫 {spider.name} 失败: {str(e)}")

    start_group_spiders.short_description = "启动选中的爬虫组"


class JobNodeFilter(ProjectNodeFilter):
    parameter_name = "spider__project__node__id"


class JobProjectFilter(ProjectFilter):
    parameter_name = "spider__project__name"
    node_filter = JobNodeFilter


class JobSpiderFilter(CustomFilter):
    """右侧过滤：Spider（爬虫）"""
    title = "爬虫"
    parameter_name = "spider__id"

    def lookups(self, request, model_admin):
        node_id = request.GET.get(JobNodeFilter.parameter_name)
        project_name = request.GET.get(JobProjectFilter.parameter_name)
        if node_id and project_name:
            spiders = models.Spider.objects.filter(project__node_id=node_id, project__name=project_name).order_by("name")
            return [(str(s.id), s.name) for s in spiders]
        return []


@admin.register(models.Job)
class JobAdmin(admin.ModelAdmin):
    list_display = (
        "job_id", "job_project_version", "job_spider", "start_time", "end_time", "status", "pid"
    )
    readonly_fields = ("create_time", "update_time", "start_time", "end_time", "pid", "log_url", "items_url", "spider", "status")
    list_filter = ("status", JobNodeFilter, JobProjectFilter)
    actions = ["start_jobs", "stop_jobs"]
    ordering = ("-status", "-start_time")

    def job_node(self, obj):
        return obj.spider.project.node.name
    job_node.admin_order_field = "spider__project__node__name"
    job_node.short_description = "节点名称"

    def job_project(self, obj):
        return obj.spider.project.name
    job_project.admin_order_field = "spider__project__name"
    job_project.short_description = "项目名称"

    def job_project_version(self, obj):
        return obj.spider.project.version
    job_project_version.admin_order_field = "spider__project__version"
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
        scrapyd_api.list_jobs(node)
        return super().get_queryset(request).prefetch_related("spider", "spider__project", "spider__project__node")

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
