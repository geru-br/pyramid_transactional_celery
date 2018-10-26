# -*- coding: utf-8 -*-
from functools import partial
import threading
import transaction
from zope.interface import implementer
from transaction.interfaces import IDataManager
from celery.app import app_or_default


__all__ = [
    'CeleryDataManager',
    'TransactionalTask',
    'task_tm',
]


# New Celery structure complicates all of this in the name of flexibility
celery_app = app_or_default()
Task = celery_app.create_task_cls()
base_task = celery_app.task


# Thread-local data
_thread_data = threading.local()


def _remove_manager(*args):
    try:
        del _thread_data.task_manager
    except AttributeError:
        pass


def _get_manager():
    task_manager = getattr(_thread_data, 'task_manager', None)
    if task_manager is None:
        task_manager = _thread_data.task_manager = CeleryDataManager()

    tx = transaction.get()
    tx.join(task_manager)
    tx.addAfterCommitHook(_remove_manager)
    return task_manager


@implementer(IDataManager)
class CeleryDataManager(object):
    transaction_manager = None

    def __init__(self):
        self.queued_tasks = []
        self.in_commit = False

    def _discard_tasks(self):
        """Discard all pending thread-local Celery tasks."""
        self.queued_tasks = []

    def _cleanup(self):
        self.queued_tasks = []

    def append(self, task):
        self.queued_tasks.append(task)

    def commit(self, trans):
        self.in_commit = True

    def sortKey(self):
        return str(id(self))

    def tpc_begin(self, transaction):
        pass

    def tpc_vote(self, trans):
        pass

    def tpc_finish(self, transaction):
        while self.queued_tasks:
            task_instance, args, kwargs = self.queued_tasks.pop(0)
            task_instance.apply_async(*args, call_from_tpc_finish=True, **kwargs)

        self.in_commit = False
        self._cleanup()

    def tpc_abort(self, transaction):
        self._discard_tasks()
        self.in_commit = False

    abort = tpc_abort


class TransactionalTask(Task):
    """A task whose execution is delayed until the current transaction gets committed."""

    def apply_async(self, *args, **kwargs):
        retries_count = kwargs.get('retries', 0)
        call_from_tpc_finish = kwargs.get('call_from_tpc_finish', None)

        if retries_count == 0 and not(call_from_tpc_finish):
            _get_manager().append((self, args, kwargs))
        else:
            return super(TransactionalTask, self).apply_async(*args, **kwargs)

task_tm = partial(base_task, base=TransactionalTask)
