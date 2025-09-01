from django import forms
from django.contrib.admin import widgets
from . import models


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
            instance.project = instance.node.default_project if instance.node else None
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
