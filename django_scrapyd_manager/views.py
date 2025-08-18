# scrapyd_manager/views.py
from django.shortcuts import redirect, get_object_or_404
from django.contrib import messages
from django.urls import reverse
from django.http import JsonResponse, HttpResponseBadRequest
from .models import Client, Project, RunHistory, Deploy
from . import scrapyd_api
import json

def admin_start_spider(request, runhistory_id):
    """
    Start spider for a given RunHistory (or Task) record.
    Expects runhistory_id pointing to a Task-like config. For demo: use RunHistory as placeholder.
    """
    rh = get_object_or_404(RunHistory, pk=runhistory_id)
    client = rh.client
    project = rh.project.name
    spider = rh.spider
    args = {}
    if rh.args:
        try:
            args = json.loads(rh.args)
        except Exception:
            pass
    result = scrapyd_api.schedule_spider(client, project, spider, args=args)
    jobid = result.get("jobid")
    if jobid:
        rh.job_id = jobid
        rh.status = "running"
        rh.save()
        messages.success(request, f"Started: {jobid}")
    else:
        messages.error(request, f"Start failed: {result}")
    return redirect(reverse("admin:scrapyd_manager_runhistory_changelist"))

def admin_cancel_spider(request, runhistory_id):
    rh = get_object_or_404(RunHistory, pk=runhistory_id)
    if not rh.job_id:
        messages.error(request, "此记录没有 job_id，无法停止")
        return redirect(reverse("admin:scrapyd_manager_runhistory_changelist"))
    result = scrapyd_api.cancel_spider(rh.client, rh.project.name, rh.job_id)
    if result.get("status") == "ok":
        rh.status = "canceled"
        rh.end_time = rh.end_time or None
        rh.save()
        messages.success(request, "已取消")
    else:
        messages.error(request, f"取消失败: {result}")
    return redirect(reverse("admin:scrapyd_manager_runhistory_changelist"))
