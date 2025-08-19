# scrapyd_manager/admin.py
from django.contrib import admin, messages
from django.contrib.admin.views.main import ChangeList
from django.shortcuts import get_object_or_404
from datetime import datetime
from . import models
from . import scrapyd_api


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

    def lookups(self, request, model_admin):
        return [(str(n.id), n.name) for n in models.Node.objects.all().order_by("name")]


class ProjectFilter(CustomFilter):
    """右侧过滤：Project（项目）"""
    title = "项目"
    parameter_name = "name"

    def lookups(self, request, model_admin):
        node_id = request.GET.get(ProjectNodeFilter.parameter_name)
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


class SpiderNodeFilter(CustomFilter):
    """右侧过滤：Node（节点）"""
    title = "节点"
    # 使用与 Django 内置 FieldListFilter 一致的参数名，便于复用你已有的默认逻辑
    parameter_name = "project__node_id"

    default_node = models.Node.objects.order_by('name').first()

    def lookups(self, request, model_admin):
        # 列出所有节点
        return [(str(n.id), n.name) for n in models.Node.objects.all().order_by("name")]

    def value(self):
        value = super().value()
        if value is None:
            if self.default_node:
                return str(self.default_node.id)
        return value


class ProjectNameFilter(CustomFilter):
    """右侧过滤：Project（按项目名，不含版本），受 Node 选择联动"""
    title = "项目"
    parameter_name = "project__name"

    default_project = models.Project.objects.filter(node=SpiderNodeFilter.default_node).first()

    def lookups(self, request, model_admin):
        # 受已选 Node 影响，只展示该 Node 下的项目名（去重）
        node_id = request.GET.get(SpiderNodeFilter.parameter_name)
        qs = models.Project.objects.all()
        if node_id:
            qs = qs.filter(node__id=node_id)
        names = qs.values_list("name", flat=True).distinct().order_by("name")
        return [(name, name) for name in names]

    def value(self):
        value = super().value()
        if value is None:
            first_project = models.Project.objects.filter(node=SpiderNodeFilter.default_node).order_by('name').first()
            if first_project:
                return first_project.name
        return value



class ProjectVersionFilter(CustomFilter):
    """右侧过滤：Version（项目版本），受 Node + Project 选择联动"""
    title = "版本"
    parameter_name = "project_version"

    def lookups(self, request, model_admin):
        node_id = request.GET.get(SpiderNodeFilter.parameter_name)
        project_name = request.GET.get(ProjectNameFilter.parameter_name)
        qs = models.Project.objects.all()
        if node_id:
            qs = qs.filter(node__id=node_id)
        if project_name:
            qs = qs.filter(name=project_name)
        versions = qs.values_list("version", flat=True).distinct().order_by("version")
        # 过滤空值，避免出现空选项
        return [(v, f'{v}({datetime.fromtimestamp(int(v))})') for v in versions if v]

    def queryset(self, request, queryset):
        project__version = self.value()
        if project__version:
            return queryset.filter(project__version=project__version)
        return queryset


@admin.register(models.Spider)
class SpiderAdmin(admin.ModelAdmin):
    list_display = ("name", "project_name", "project_node_name", "create_time")
    readonly_fields = ("create_time", "update_time")
    list_filter = (SpiderNodeFilter, ProjectNameFilter, ProjectVersionFilter)

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
        if node_id:
            node = get_object_or_404(models.Node, pk=node_id)
        else:
            node = SpiderNodeFilter.default_node
        project_id = request.GET.get(ProjectNameFilter.parameter_name)
        if project_id:
            project = models.Project.objects.get(pk=project_id)
        else:
            project = models.Project.objects.filter(node=node).first()
        if project:
            scrapyd_api.list_project_spiders(project)
        return super().get_queryset(request)

    def get_object(self, request, object_id, from_field = ...):
        return get_object_or_404(models.Spider, pk=object_id)


@admin.register(models.SpiderGroup)
class SpiderGroupAdmin(admin.ModelAdmin):
    list_display = ("name", "description", "create_time")
    readonly_fields = ("create_time", "update_time")
    filter_horizontal = ("spiders",)


class JobNodeFilter(CustomFilter):
    """右侧过滤：Node（节点）"""
    title = "节点"
    parameter_name = "spider__project__node__id"

    def lookups(self, request, model_admin):
        return [(str(n.id), n.name) for n in models.Node.objects.all().order_by("name")]


class JobProjectFilter(CustomFilter):
    """右侧过滤：Project（项目）"""
    title = "项目"
    parameter_name = "spider__project__name"

    def lookups(self, request, model_admin):
        node_id = request.GET.get("spider__project__node__id")
        if node_id:
            project_names = models.Project.objects.filter(node_id=node_id).values_list("name", flat=True).distinct().order_by("name")
            return [(name, name) for name in project_names]
        return []


class JobSpiderFilter(CustomFilter):
    """右侧过滤：Spider（爬虫）"""
    title = "爬虫"
    parameter_name = "spider__id"

    def lookups(self, request, model_admin):
        node_id = request.GET.get("spider__project__node__id")
        project_id = request.GET.get("spider__project__id")
        if node_id and project_id:
            spiders = models.Spider.objects.filter(project__node_id=node_id, project_id=project_id).order_by("name")
            return [(str(s.id), s.name) for s in spiders]
        return []


@admin.register(models.Job)
class JobAdmin(admin.ModelAdmin):
    list_display = (
        "job_id", "job_spider", "start_time", "end_time", "status", "pid"
    )
    readonly_fields = ("create_time", "update_time", "start_time", "end_time", "pid", "log_url", "items_url", "spider", "status")
    list_filter = ("status", JobNodeFilter, JobProjectFilter, JobSpiderFilter)
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

    def job_spider(self, obj):
        return obj.spider.name
    job_spider.admin_order_field = "spider__name"
    job_spider.short_description = "爬虫名称"

    def stop_jobs(self, request, queryset):
        """
        停止选中的爬虫任务
        """
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
        node_id = request.GET.get("spider__project__node__id")
        if node_id:
            node = get_object_or_404(models.Node, pk=node_id)
        else:
            return models.Job.objects.none()
        scrapyd_api.list_jobs(node)
        return super().get_queryset(request).prefetch_related("spider", "spider__project", "spider__project__node")

    def get_object(self, request, object_id, from_field = ...):
        return get_object_or_404(models.Job, pk=object_id)

