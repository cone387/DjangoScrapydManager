from django.apps import AppConfig
import logging


class DjangoScrapydManagerConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'django_scrapyd_manager'

    def ready(self):
        logger = logging.getLogger(self.name)
        if not logger.handlers:  # 避免重复添加
            handler = logging.StreamHandler()
            formatter = logging.Formatter("[%(levelname)s] %(asctime)s %(name)s %(message)s")
            handler.setFormatter(formatter)
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)