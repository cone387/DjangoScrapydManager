from django import forms
from django.contrib.admin import widgets
from . import models


# class SpiderGroupForm(forms.ModelForm):
#     # 用多选框来选择 spider，choices 按 name 去重
#     spiders_select = forms.ModelMultipleChoiceField(
#         queryset=models.Spider.objects.none(),
#         widget=widgets.FilteredSelectMultiple("爬虫", is_stacked=False),
#         required=False,
#     )
#
#     # version_select = forms.ChoiceField(
#     #     choices=[],
#     #     required=True,
#     #     label="版本"
#     # )
#     kwargs = forms.JSONField(required=False, initial={})
#
#     class Meta:
#         model = models.SpiderGroup
#         fields = "__all__"
#
#     def __init__(self, *args, **kwargs):
#         super().__init__(*args, **kwargs)
#
#         # 只保留每个 spider.name 的一个，去重
#         seen = set()
#         unique_spiders = []
#         for spider in models.Spider.objects.all().order_by("name"):
#             if spider.name not in seen:
#                 seen.add(spider.name)
#                 unique_spiders.append(spider.id)
#         self.fields["spiders_select"].queryset = models.Spider.objects.filter(id__in=unique_spiders)
#
#         # all_versions = models.ProjectVersion.objects.all()
#         # version_choices = [(None, "自动最新版本")] + [(v, v.full_path) for v in all_versions]
#         self.fields["version"].empty_label = "自动最新版本"
#         self.fields["version"].label_from_instance = lambda obj: obj.full_path
#         # 初始值，把 JSONField 里的 spider.name 映射回选中的 spider
#         if self.instance and self.instance.spiders:
#             spider_names = [s.get("name") for s in self.instance.spiders]
#             self.initial["spiders_select"] = models.Spider.objects.filter(name__in=spider_names)
#
#         # if self.instance:
#         #     self.initial["version"] = self.instance.version
#
#     def clean(self):
#         cleaned_data = super().clean()
#         version = cleaned_data.get("version")
#         if version == "latest":
#             cleaned_data["version"] = None
#         spiders = []
#         spiders_select = self.cleaned_data.pop("spiders_select")
#         for spider in spiders_select:
#             spiders.append({"name": spider.name})
#         cleaned_data["spiders"] = spiders
#         return cleaned_data
#
#     def save(self, commit=True):
#         obj: models.SpiderGroup = super().save(commit=False)
#         obj.spiders = self.cleaned_data.get("spiders")
#         if commit:
#             obj.save()
#         return obj

class SpiderGroupForm(forms.ModelForm):
    spiders_select = forms.ModelMultipleChoiceField(
        queryset=models.Spider.objects.none(),
        widget=widgets.FilteredSelectMultiple("爬虫", is_stacked=False),
        required=False,
    )

    class Meta:
        model = models.SpiderGroup
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        node = getattr(self.instance, "node", None)
        project = getattr(self.instance, "project", None)
        version = getattr(self.instance, "version", None)

        self.fields["version"].empty_label = "自动最新版本"
        self.fields["version"].label_from_instance = lambda obj: obj.short_path

        if node:
            self.fields["project"].queryset = models.Project.objects.filter(node=node)

        spiders = models.Spider.objects.all()
        if version:
            spiders = spiders.filter(version=version)
        elif project:
            spiders = spiders.filter(version__project=project)
            latest_version = models.ProjectVersion.objects.filter(project=project).first()
            if latest_version:
                self.fields["version"].empty_label = f"自动最新版本[{latest_version.short_path}]"

        self.fields["spiders_select"].queryset = spiders

    def clean(self):
        cleaned_data = super().clean()
        spiders = []
        spiders_select = self.cleaned_data.pop("spiders_select")
        for spider in spiders_select:
            spiders.append({"name": spider.name})
        cleaned_data["spiders"] = spiders
        return cleaned_data