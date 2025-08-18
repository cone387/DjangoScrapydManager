# scrapyd_manager/admin.py
from django.contrib import admin, messages
from django.contrib.admin.views.main import ChangeList
from django.shortcuts import get_object_or_404
from . import models
from . import scrapyd_api


@admin.register(models.Node)
class NodeAdmin(admin.ModelAdmin):
    list_display = ("name", "ip", "port", "description", "auth", "create_time")
    readonly_fields = ("create_time", "update_time")


class DefaultProjectChangeList(ChangeList):
    def get_filters_params(self, params=None):
        params = super().get_filters_params(params)
        if 'node__id__exact' not in params:
            node_id = models.Node.objects.all().values_list('id', flat=True).first()
            if node_id:
                params['node__id__exact'] = [node_id]
        return params


@admin.register(models.Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("name", "version", "create_time")
    readonly_fields = ("create_time", "update_time")
    list_filter = ("node", )

    def get_changelist(self, request, **kwargs):
        return DefaultProjectChangeList

    def get_queryset(self, request):
        node_id = request.GET.get("node__id__exact")
        if node_id:
            node = get_object_or_404(models.Node, pk=node_id)
        else:
            return models.Project.objects.none()
        scrapyd_api.list_node_projects(node)
        return super().get_queryset(request)

    def get_object(self, request, object_id, from_field = ...):
        return get_object_or_404(models.Project, pk=object_id)


class DefaultSpiderChangeList(ChangeList):
    def get_filters_params(self, params=None):
        params = super().get_filters_params(params)
        # if 'project__node__id__exact' not in params:
        #     node_id = models.Node.objects.all().values_list('id', flat=True).first()
        #     if node_id:
        #         params['project__node__id__exact'] = [node_id]
        return params


class NodeFilter(admin.SimpleListFilter):
    """右侧过滤：Node（节点）"""
    title = "节点"
    # 使用与 Django 内置 FieldListFilter 一致的参数名，便于复用你已有的默认逻辑
    parameter_name = "node_id"

    def lookups(self, request, model_admin):
        # 列出所有节点
        return [(str(n.id), n.name) for n in models.Node.objects.all().order_by("name")]

    def queryset(self, request, queryset):
        node_id = self.value()
        if node_id:
            return queryset.filter(id=node_id)
        return queryset


class ProjectNameFilter(admin.SimpleListFilter):
    """右侧过滤：Project（按项目名，不含版本），受 Node 选择联动"""
    title = "项目"
    parameter_name = "project__name"

    def lookups(self, request, model_admin):
        # 受已选 Node 影响，只展示该 Node 下的项目名（去重）
        node_id = request.GET.get("project__node__id__exact")
        qs = models.Project.objects.all()
        if node_id:
            qs = qs.filter(node__id=node_id)
        names = qs.values_list("name", flat=True).distinct().order_by("name")
        return [(name, name) for name in names]

    def queryset(self, request, queryset):
        name = self.value()
        if name:
            return queryset.filter(project__name=name)
        return queryset


# class ProjectVersionFilter(admin.SimpleListFilter):
#     """右侧过滤：Version（项目版本），受 Node + Project 选择联动"""
#     title = "版本"
#     parameter_name = "version"
#
#     def lookups(self, request, model_admin):
#         node_id = request.GET.get("project__node__id__exact")
#         project_name = request.GET.get("project__name__exact")
#         qs = models.Project.objects.all()
#         if node_id:
#             qs = qs.filter(node__id=node_id)
#         if project_name:
#             qs = qs.filter(name=project_name)
#         versions = qs.values_list("version", flat=True).distinct().order_by("version")
#         # 过滤空值，避免出现空选项
#         return [(v, v) for v in versions if v]
#
#     def queryset(self, request, queryset):
#         version = self.value()
#         if version:
#             return queryset.filter(project__version=version)
#         return queryset

class VersionListFilter(admin.SimpleListFilter):
    title = "Version"
    parameter_name = "project_version"

    def lookups(self, request, model_admin):
        versions = models.Project.objects.values_list("version", flat=True).distinct()
        return [(v, v) for v in versions if v]

    def queryset(self, request, queryset):
        value = self.value()
        if not value:
            default = models.Project.objects.values_list("version", flat=True).order_by("version").first()
            if default:
                return queryset.filter(project__version=default)
            return queryset
        return queryset.filter(project__version=value)


@admin.register(models.Spider)
class SpiderAdmin(admin.ModelAdmin):
    list_display = ("name", "project__name", "project__node__name", "create_time")
    readonly_fields = ("create_time", "update_time")
    list_filter = (NodeFilter, ProjectNameFilter, VersionListFilter)

    def get_changelist(self, request, **kwargs):
        return DefaultSpiderChangeList

    def get_queryset(self, request):
        node_id = request.GET.get("project__node__id__exact")
        if node_id:
            node = get_object_or_404(models.Node, pk=node_id)
        else:
            return models.Spider.objects.none()
        project_id = request.GET.get("project__id__exact")
        if project_id:
            project = get_object_or_404(models.Project, pk=project_id)
        else:
            return models.Spider.objects.none()
        scrapyd_api.list_project_spiders(project)
        return super().get_queryset(request)

    def get_object(self, request, object_id, from_field = ...):
        return get_object_or_404(models.Spider, pk=object_id)


@admin.register(models.SpiderGroup)
class SpiderGroupAdmin(admin.ModelAdmin):
    list_display = ("name", "description", "create_time")
    readonly_fields = ("create_time", "update_time")
    filter_horizontal = ("spiders",)


@admin.register(models.Job)
class JobAdmin(admin.ModelAdmin):
    list_display = (
        "spider", "job_id", "start_time", "end_time", "status", "pid", "create_time"
    )
    readonly_fields = ("create_time", "update_time")
    list_filter = ("spider__project__node", "spider__project", "status")
    actions = ["start_jobs", "stop_jobs"]

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
        node_id = request.GET.get("spider__project__node__id__exact")
        if node_id:
            node = get_object_or_404(models.Node, pk=node_id)
        else:
            return models.Job.objects.none()
        scrapyd_api.list_jobs(node)
        return super().get_queryset(request)

    def get_object(self, request, object_id, from_field = ...):
        return get_object_or_404(models.Job, pk=object_id)

