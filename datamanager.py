import os
import shutil
import logging


logger = logging.getLogger(__name__)


class FileDataManager(object):
    'Uses Two-Phase Commit protocol to allow moving files just after the '
    'current transaction has been commited.'

    def __init__(self):
        self.queue = []

    def put(self, source, destination):
        self.queue.append((source, destination))

    def __eq__(self, other):
        if not isinstance(other, FileDataManager):
            return NotImplemented
        return True

    def abort(self, trans):
        self._finish()

    def tpc_begin(self, trans):
        pass

    def commit(self, trans):
        pass

    def tpc_vote(self, trans):
        pass

    def tpc_finish(self, trans):
        'Moves source files to destination'
        for source, destination in self.queue:
            if os.path.exists(destination):
                os.remove(destination)
            try:
                shutil.move(source, destination)
            except (shutil.SameFileError, OSError) as error:
                logger.exception(error)

        self._finish()

    def tpc_abort(self, trans):
        self._finish()

    def _finish(self):
        self.queue = []
