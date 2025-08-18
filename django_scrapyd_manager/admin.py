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
        projects = scrapyd_api.list_projects(node)
        items = []
        for project in projects:
            items.append(models.Project(**project))
        models.Project.objects.bulk_create(items, ignore_conflicts=True)
        return super().get_queryset(request)

    def get_object(self, request, object_id, from_field = ...):
        return get_object_or_404(models.Project, pk=object_id)


class DefaultSpiderChangeList(ChangeList):
    def get_filters_params(self, params=None):
        params = super().get_filters_params(params)
        if 'project__node__id__exact' not in params:
            node_id = models.Node.objects.all().values_list('id', flat=True).first()
            if node_id:
                params['project__node__id__exact'] = [node_id]
        return params


@admin.register(models.Spider)
class SpiderAdmin(admin.ModelAdmin):
    list_display = ("name", "project", "create_time")
    readonly_fields = ("create_time", "update_time")
    list_filter = ("project__node", "project")

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
        spiders = scrapyd_api.list_spiders(node, project.name)
        items = []
        for spider in spiders:
            items.append(models.Spider(project=project, **spider))
        models.Spider.objects.bulk_create(items, ignore_conflicts=True)
        return super().get_queryset(request)

    def get_object(self, request, object_id, from_field = ...):
        return get_object_or_404(models.Spider, pk=object_id)


@admin.register(models.Job)
class JobAdmin(admin.ModelAdmin):
    list_display = (
        "spider", "job_id", "start_time", "end_time", "status", "pid", "create_time"
    )
    readonly_fields = ("create_time", "update_time")
    list_filter = ("spider__project__node", "spider__project", "status")
    actions = ["start_jobs", "stop_jobs"]


    def start_jobs(self, request, queryset):
        """
        启动选中的爬虫任务
        """
        if not queryset:
            messages.error(request, "请选择要启动的任务")
            return

        for job in queryset:
            node = job.spider.project.node
            try:
                scrapyd_api.start_spider(node, job.spider.project.name, job.spider.name)
                messages.success(request, f"成功启动任务 {job.job_id} ({job.spider.name})")
            except Exception as e:
                messages.error(request, f"启动任务 {job.job_id} 失败: {str(e)}")
    start_jobs.short_description = "启动选中的爬虫任务"


    def stop_jobs(self, request, queryset):
        """
        停止选中的爬虫任务
        """
        if not queryset:
            messages.error(request, "请选择要停止的任务")
            return

        for job in queryset:
            node = job.spider.project.node
            try:
                scrapyd_api.stop_spider(node, job.job_id)
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
        project_id = request.GET.get("spider__project__id__exact")
        if project_id:
            project = get_object_or_404(models.Project, pk=project_id)
        else:
            return models.Job.objects.none()
        jobs = scrapyd_api.list_jobs(node, project.name)
        items = []
        for job in jobs:
            items.append(models.Job(spider=models.Spider(project=project), **job))
        models.Job.objects.bulk_create(items, ignore_conflicts=True)
        return super().get_queryset(request)

    def get_object(self, request, object_id, from_field = ...):
        return get_object_or_404(models.Job, pk=object_id)

