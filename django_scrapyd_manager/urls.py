# scrapyd_manager/urls.py
from django.urls import path
from . import views


urlpatterns = [
    path("admin/start/<int:runhistory_id>/", views.admin_start_spider, name="admin_start_spider"),
    path("admin/stop/<int:runhistory_id>/", views.admin_cancel_spider, name="admin_cancel_spider"),
]
