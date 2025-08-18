from typing import Set, Dict, Any
from django.db import models
from django.db.models import Q
from functools import cmp_to_key
from . import scrapyd_api


class BaseQuerySet(list):
    class Query:
        order_by = []
        select_related = False

    def using(self, alias):
        return self

    def all(self):
        return self

    def __init__(self, model):
        super().__init__()
        self.query = self.Query
        self.model = model
        self._prefetch_related_lookups = True
        self.verbose_name = model._meta.verbose_name
        self.verbose_name_plural = model._meta.verbose_name_plural
        self._filters = []
        self._cache_result = None


    @staticmethod
    def _match_condition(obj, key, value):
        """匹配单个条件"""
        value = str(value)
        try:
            column, op = key.rsplit('__', 1)
        except ValueError:
            column, op = key, 'exact'

        def get_attr(x):
            for attr in column.split('__'):
                x = getattr(x, attr, '')
            return str(x)

        if op == 'in':
            return get_attr(obj) in value
        elif op == 'exact':
            return get_attr(obj) == value
        else:
            return False

    def _match_q(self, obj, q: Q):
        """递归匹配 Q 对象"""
        if not isinstance(q, Q):
            return True

        if q.connector == Q.AND:
            return all(
                self._match_q(obj, child) if isinstance(child, Q) else self._match_condition(obj, *child)
                for child in q.children
            )
        elif q.connector == Q.OR:
            return any(
                self._match_q(obj, child) if isinstance(child, Q) else self._match_condition(obj, *child)
                for child in q.children
            )
        else:
            return False

    def _apply_filter(self, data, args, kwargs):
        # 处理 kwargs
        for k, v in kwargs.items():
            data = self.__class__([x for x in data if self._match_condition(x, k, v)], self.model)

        # 处理 Q 对象
        for q in args:
            data = self.__class__([x for x in data if self._match_q(x, q)], self.model)

        return data

    def filter(self, *args, **kwargs):
        # clone = self._clone()
        self._filters.append((args, kwargs))
        return self

    def translate_filters(self) -> Dict[str, Any]:
        return dict(self._filters)

    def _fetch(self):
        params = self.translate_filters()
        return self.query_queryset(params)

    def fetch(self):
        if self._cache_result:
            return self._cache_result
        self._cache_result = self._fetch()
        return self._cache_result

    def query_queryset(self, params):
        raise NotImplementedError()

    def _clone(self):
        # clone = self.__class__(self, self.model)
        # self._filters = self._filters[:]
        return self

    def __iter__(self):
        return iter(self.fetch())

    def __len__(self):
        return len(self._fetch())


    # def filter(self, *args, **kwargs) -> 'VirtualQuerySet':
    #
    #
    #     queryset = self
    #
    #     # 处理 kwargs
    #     for k, v in kwargs.items():
    #         queryset = self.__class__([x for x in queryset if self._match_condition(x, k, v)], self.model)
    #
    #     # 处理 Q 对象
    #     for q in args:
    #         queryset = self.__class__([x for x in queryset if self._match_q(x, q)], self.model)
    #
    #     return queryset

    # def filter(self, *args, **kwargs) -> 'VirtualQuerySet':
    #     queryset = self
    #     for k, v in kwargs.items():
    #         v = str(v)
    #         try:
    #             column, op = k.rsplit('__', 1)
    #         except ValueError:
    #             column, op = k, 'exact'
    #
    #         def get_attr(x):
    #             for attr in column.split('__'):
    #                 x = getattr(x, attr, '')
    #             return str(x)
    #
    #         if op == 'in':
    #             queryset = self.__class__([x for x in queryset if get_attr(x) in v], self.model)
    #         elif op == 'exact':
    #             queryset = self.__class__([x for x in queryset if get_attr(x) == v], self.model)
    #     return queryset

    def order_by(self, *args) -> 'BaseQuerySet':
        def custom_sort(x, y):
            for arg in args:
                if arg.startswith('-'):
                    arg = arg[1:]
                    reverse = True
                else:
                    reverse = False
                if getattr(x, arg) > getattr(y, arg):
                    return -1 if reverse else 1
                elif getattr(x, arg) < getattr(y, arg):
                    return 1 if reverse else -1
            return 0
        self.sort(key=cmp_to_key(custom_sort))
        return self

    def select_related(self, *_) -> 'BaseQuerySet':
        return self

    def count(self, value=None) -> int:
        return len(self)

    def delete(self):
        for x in self:
            x.delete()

    def get(self, **kwargs):
        for x in self:
            for k, v in kwargs.items():
                if getattr(x, k) != v:
                    break
            else:
                return x
        raise self.model.DoesNotExist


class ProjectQuerySet(BaseQuerySet):
    _cache = {}

    def __init__(self, model):
        super().__init__(model)

    def data_to_model(self, node, data: dict):
        data.setdefault("id", f"{node.id}···{data.get('name', '')}···{data.get('version', '')}")
        model = self.model(node=node, **data)
        return model

    def get(self, **kwargs):
        project_id: str = kwargs.pop('id', '')
        params = {}
        if project_id:
            node_id, name, version = project_id.split('···')
            params['node_id'] = int(node_id)
            kwargs['node_id'] = int(node_id)
            kwargs['name'] = name
            kwargs['version'] = version
        queryset = self.query_queryset(params)
        for x in queryset:
            for k, v in kwargs.items():
                if getattr(x, k) != v:
                    break
            else:
                return x
        raise self.model.DoesNotExist

    def query_queryset(self, params):
        # 这里可以实现具体的 fetch 逻辑
        node_id = params.get("node_id")
        node_model = self.model._meta.get_field('node').remote_field.model
        if node_id:
            node = node_model.objects.get(pk=node_id)
        else:
            node = node_model.objects.first()
        projects = scrapyd_api.list_projects(node)
        self._cache[node.id] = cache = [self.data_to_model(node, x) for x in projects]
        return cache

    def translate_filters(self):
        """
        解析 self._filters -> dict，提取 node_id / project / version
        """
        result = {}

        for q_filters, _ in self._filters:
            if not q_filters:
                continue

            q = q_filters[0]  # 取 Q 对象
            children = getattr(q, "children", [])

            for key, value in children:
                if key == "node__id__exact":
                    result["node_id"] = str(value)
                elif key == "name__exact":  # project 名称
                    result["project"] = value
                elif key == "version__exact":
                    result["version"] = value

        return result


class BaseManager(models.Manager):
    queryset_class = BaseQuerySet


    def all(self) -> 'BaseQuerySet':
        return self.queryset_class(self.model)

    def get_queryset(self):
        return self.all()

    def get(self, pk, default=None):
        return self.all().get(pk=pk, default=default)

    def filter(self, *args, **kwargs) -> 'BaseQuerySet':
        return self.all().filter(*args, **kwargs)

    def none(self):
        return self.queryset_class(self.model)

    # def count(self):
    #     return len(self)

    def __get__(self, instance, owner):
        return self._meta.managers_map[self.manager.name]


