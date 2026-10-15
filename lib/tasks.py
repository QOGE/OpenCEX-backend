import enum
import importlib
import logging
from decimal import Decimal

from django.utils.functional import Promise

from .exceptions import BaseError


log = logging.getLogger(__name__)

# Only BaseError subclasses in these modules can be reconstructed.
_ALLOWED_EXC_PREFIXES = (
    'core.exceptions.',
    'lib.exceptions',
    'bots.exceptions',
    'cryptocoins.exceptions',
    'core.models.inouts.withdrawal',
)


class WrappedTaskResultStatus(enum.IntEnum):
    ERROR = 0
    OK = 1


class WrappedTaskManager:

    @classmethod
    def wrap_fn(cls, fn, *args, **kwargs):
        try:
            return WrappedTaskManager.pack_result(fn(*args, **kwargs))
        except Exception as exc:
            return WrappedTaskManager.pack_exception(exc)

    @classmethod
    def pack_result(cls, data=None) -> dict:
        return {
            'status': WrappedTaskResultStatus.OK.value,
            'data': data,
        }

    @classmethod
    def pack_exception(cls, exc) -> dict:
        if not isinstance(exc, BaseError):
            raise exc

        args = getattr(exc, '_args', ())
        kwargs = getattr(exc, '_kwargs', {})
        return {
            'status': WrappedTaskResultStatus.ERROR.value,
            'type': cls._pack_exc_type(type(exc)),
            'args': cls._to_jsonable(args),
            'kwargs': cls._to_jsonable(kwargs),
        }

    @classmethod
    def unpack_result_or_raise(cls, result: dict):
        if result['status'] == WrappedTaskResultStatus.OK:
            return result['data']

        exc_type = cls._unpack_exc_type(result['type'])
        args = cls._from_jsonable(result.get('args') or ())
        kwargs = cls._from_jsonable(result.get('kwargs') or {})
        if not isinstance(args, (list, tuple)):
            args = (args,)
        if not isinstance(kwargs, dict):
            kwargs = {}
        raise exc_type(*tuple(args), **kwargs)

    @staticmethod
    def _pack_exc_type(exc_type):
        name = '{}.{}'.format(exc_type.__module__, exc_type.__qualname__)
        if not WrappedTaskManager._exc_type_allowed(name):
            raise TypeError('Cannot pack exception type {}'.format(name))
        return name

    @staticmethod
    def _unpack_exc_type(name):
        if not isinstance(name, str) or not WrappedTaskManager._exc_type_allowed(name):
            raise ValueError('Invalid packed exception type')
        module_name, cls_name = name.rsplit('.', 1)
        if not cls_name.isidentifier() or '.' in cls_name:
            raise ValueError('Invalid packed exception type')
        module = importlib.import_module(module_name)
        cls = getattr(module, cls_name)
        if not isinstance(cls, type) or not issubclass(cls, BaseError) or cls is BaseError:
            raise ValueError('Packed type is not a BaseError subclass')
        return cls

    @staticmethod
    def _exc_type_allowed(name):
        return any(
            name == prefix or name.startswith(prefix if prefix.endswith('.') else prefix + '.')
            for prefix in _ALLOWED_EXC_PREFIXES
        )

    @classmethod
    def _to_jsonable(cls, obj):
        if obj is None or isinstance(obj, (bool, int, float, str)):
            return obj
        if isinstance(obj, Decimal):
            return {'__decimal__': str(obj)}
        if isinstance(obj, Promise):
            return str(obj)
        if isinstance(obj, (list, tuple)):
            return [cls._to_jsonable(item) for item in obj]
        if isinstance(obj, dict):
            return {str(key): cls._to_jsonable(value) for key, value in obj.items()}
        from core.currency import Currency
        if isinstance(obj, Currency):
            return {'__currency__': obj.code}
        from core.models.inouts.pair import Pair
        if isinstance(obj, Pair):
            return {'__pair__': obj.code}
        raise TypeError('Cannot JSON-pack {}'.format(type(obj).__name__))

    @classmethod
    def _from_jsonable(cls, obj):
        if isinstance(obj, dict):
            if set(obj.keys()) == {'__decimal__'}:
                return Decimal(obj['__decimal__'])
            if set(obj.keys()) == {'__currency__'}:
                from core.currency import Currency
                return Currency.get(obj['__currency__'])
            if set(obj.keys()) == {'__pair__'}:
                from core.models.inouts.pair import Pair
                return Pair.get(obj['__pair__'])
            return {key: cls._from_jsonable(value) for key, value in obj.items()}
        if isinstance(obj, list):
            return [cls._from_jsonable(item) for item in obj]
        return obj
