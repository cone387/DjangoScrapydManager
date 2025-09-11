from django.apps import AppConfig
import logging
import threading
import sys


class DjangoScrapydManagerConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'django_scrapyd_manager'
    _guardian_thread_started = False

    def ready(self):
        logger = logging.getLogger(self.name)
        if not logger.handlers:  # 避免重复添加
            handler = logging.StreamHandler()
            formatter = logging.Formatter("[%(levelname)s] %(asctime)s %(name)s %(message)s")
            handler.setFormatter(formatter)
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)


        if "runserver" not in sys.argv and "gunicorn" not in sys.argv:
            return

        # 避免重复启动线程
        if self._guardian_thread_started:
            return

        from .guardian import guard_loop

        # 启动后台线程
        thread = threading.Thread(target=guard_loop, daemon=True)
        thread.start()
        self._guardian_thread_started = True
        logger.info("Guardian loop thread started")
