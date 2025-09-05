import time

from django import forms
from django.contrib.admin import widgets
from django.core.exceptions import ValidationError

from . import models
from . import scrapyd_api
from .models import Project


class SpiderGroupForm(forms.ModelForm):
    spiders_select = forms.ModelMultipleChoiceField(
        queryset=models.Spider.objects.none(),
        widget=widgets.FilteredSelectMultiple("爬虫", is_stacked=False),
        required=False,
    )

    class Meta:
        model = models.SpiderGroup
        fields = "__all__"

    def __init__(self, *args, instance=None, **kwargs):
        if instance is None:
            instance = models.SpiderGroup()
            instance.node = models.Node.default_node()
            instance.project = instance.node.default_project
        super().__init__(*args, instance=instance, **kwargs)
        instance: models.SpiderGroup = self.instance

        self.fields["project"].queryset = instance.node.projects.all()
        self.fields["version"].queryset = instance.project.versions.all()
        version = instance.version
        if not version:
            version = instance.project.latest_version
        if version:
            self.fields["version"].empty_label = f"自动最新版本[{version.short_path}]"
            spiders_select: forms.Field = self.fields["spiders_select"]
            spiders_select.queryset = version.spiders.all()
            spiders_select.initial = instance.resolved_spiders
        else:
            self.fields["version"].empty_label = "自动最新版本[暂无可用版本]"
        self.fields["version"].label_from_instance = lambda obj: obj.short_path

    def clean(self):
        cleaned_data = super().clean()
        spiders = []
        spiders_select = self.cleaned_data.pop("spiders_select")
        for spider in spiders_select:
            spiders.append({"name": spider.name})
        cleaned_data["spiders"] = spiders
        return cleaned_data

    def save(self, commit=True):
        obj: models.SpiderGroup = super().save(commit=False)
        obj.spiders = self.cleaned_data.get("spiders")
        if commit:
            obj.save()
        return obj


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
            self.fields["node"].initial = models.Node.default_node()
            try:
                project.queryset = self.fields["node"].initial.projects.all()
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
