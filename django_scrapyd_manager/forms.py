from django.contrib import admin
from django import forms
from . import models


class SpiderGroupForm(forms.ModelForm):
    # 用多选框来选择 spider，choices 按 name 去重
    spiders_select = forms.ModelMultipleChoiceField(
        queryset=models.Spider.objects.all(),
        widget=admin.widgets.FilteredSelectMultiple("爬虫", is_stacked=False),
        required=False,
    )

    version_select = forms.ChoiceField(
        choices=[],
        required=True,
        label="版本"
    )
    kwargs = forms.JSONField(required=False, initial={})

    class Meta:
        model = models.SpiderGroup
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # 只保留每个 spider.name 的一个，去重
        seen = set()
        unique_spiders = []
        for spider in models.Spider.objects.all().order_by("name"):
            if spider.name not in seen:
                seen.add(spider.name)
                unique_spiders.append(spider.id)
        self.fields["spiders_select"].queryset = models.Spider.objects.filter(id__in=unique_spiders)

        all_versions = models.Project.objects.order_by("-create_time").values_list("version", flat=True)
        all_versions = list(set(all_versions))
        version_choices = [("latest", "使用最新版本")] + [(v, v) for v in all_versions]

        self.fields["version_select"].choices = version_choices
        # 初始值，把 JSONField 里的 spider.name 映射回选中的 spider
        if self.instance and self.instance.spiders:
            spider_names = [s.get("name") for s in self.instance.spiders]
            self.initial["spiders_select"] = models.Spider.objects.filter(name__in=spider_names)

        if self.instance:
            self.initial["version_select"] = self.instance.version

    def clean(self):
        cleaned_data = super().clean()
        cleaned_data["version"] = cleaned_data.pop("version_select")
        spiders = []
        spiders_select = self.cleaned_data.pop("spiders_select")
        for spider in spiders_select:
            spiders.append({"name": spider.name})
        cleaned_data["spiders"] = spiders
        return cleaned_data

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.version = self.cleaned_data.get("version")
        obj.spiders = self.cleaned_data.get("spiders")
        if commit:
            obj.save()
        return obj