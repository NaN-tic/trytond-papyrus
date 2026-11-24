import logging


logger = logging.getLogger(__name__)


class FileDataManager:
    'Removes imported source files only after their binary data is committed.'
    def __init__(self):
        self.queue = []

    def put(self, path):
        self.queue.append(path)

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
        for path in self.queue:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.exception('Could not remove imported Papyrus source %s', path)

        self._finish()

    def tpc_abort(self, trans):
        self._finish()

    def _finish(self):
        self.queue = []
