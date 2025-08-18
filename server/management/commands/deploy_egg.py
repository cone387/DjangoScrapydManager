# scrapyd_manager/management/commands/deploy_egg.py
import os
from django.core.management.base import BaseCommand, CommandError
from django_scrapyd_manager.models import Client, Project, Deploy
from django_scrapyd_manager.scrapyd_api import add_version
from django.utils import timezone

class Command(BaseCommand):
    help = "Upload egg to scrapyd: python manage.py deploy_egg <client_id> <project_name> <egg_path> [version]"

    def add_arguments(self, parser):
        parser.add_argument('client_id', type=int)
        parser.add_argument('project_name', type=str)
        parser.add_argument('egg_path', type=str)
        parser.add_argument('--version', type=str, default=None)

    def handle(self, *args, **options):
        client_id = options['client_id']
        project_name = options['project_name']
        egg_path = options['egg_path']
        version = options.get('version') or f"v{int(timezone.now().timestamp())}"
        try:
            client = Client.objects.get(pk=client_id)
        except Client.DoesNotExist:
            raise CommandError("Client not found")
        if not os.path.exists(egg_path):
            raise CommandError("egg_path not exists")
        res = add_version(client, project_name, version, egg_path)
        if res.get("status") == "ok":
            project, _ = Project.objects.get_or_create(name=project_name)
            Deploy.objects.update_or_create(client=client, project=project, defaults={"deployed_at": timezone.now()})
            self.stdout.write(self.style.SUCCESS(f"Uploaded: {res}"))
        else:
            raise CommandError(f"Upload failed: {res}")
