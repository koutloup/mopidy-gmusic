from __future__ import unicode_literals

import logging
import time

from threading import Lock

from mopidy import backend

import pykka

from .library import GMusicLibraryProvider
from .playback import GMusicPlaybackProvider
from .playlists import GMusicPlaylistsProvider
from .repeating_timer import RepeatingTimer
from .session import GMusicSession

logger = logging.getLogger(__name__)


class GMusicBackend(pykka.ThreadingActor, backend.Backend):
    def __init__(self, config, audio):
        super(GMusicBackend, self).__init__()

        self.config = config

        self._refresh_library_rate = \
            config['gmusic']['refresh_library'] * 60.0
        self._refresh_playlists_rate = \
            config['gmusic']['refresh_playlists'] * 60.0
        self._refresh_library_timer = None
        self._refresh_playlists_timer = None
        self._refresh_lock = Lock()
        self._refresh_last = 0
        # do not run playlist refresh around library refresh
        self._refresh_threshold = self._refresh_playlists_rate * 0.3

        self.library = GMusicLibraryProvider(backend=self)
        self.playback = GMusicPlaybackProvider(audio=audio, backend=self)
        self.playlists = GMusicPlaylistsProvider(backend=self)
        self.session = GMusicSession()

        self.uri_schemes = ['gmusic']

    def on_start(self):
        self.session.login(self.config['gmusic']['username'],
                           self.config['gmusic']['password'],
                           self.config['gmusic']['deviceid'])
        self.library.set_all_access(self.config['gmusic']['all_access'])

        # wait a few seconds to let mopidy settle
        # then refresh google music content asynchronously
        self._refresh_library_timer = RepeatingTimer(
            self._refresh_library,
            self._refresh_library_rate)
        self._refresh_library_timer.start()
        # schedule playlist refresh as desired
        if self._refresh_playlists_rate > 0:
            self._refresh_playlists_timer = RepeatingTimer(
                self._refresh_playlists,
                self._refresh_playlists_rate)
            self._refresh_playlists_timer.start()

    def on_stop(self):
        if self._refresh_library_timer:
            self._refresh_library_timer.cancel()
            self._refresh_library_timer = None
        if self._refresh_playlists_timer:
            self._refresh_playlists_timer.cancel()
            self._refresh_playlists_timer = None
        self.session.logout()

    def _refresh_library(self):
        with self._refresh_lock:
            t0 = round(time.time())
            logger.info('Start refreshing Google Music library')
            self.library.refresh()
            self.playlists.refresh()
            t = round(time.time()) - t0
            logger.info('Finished refreshing Google Music content in %ds', t)
            self._refresh_last = t0

    def _refresh_playlists(self):
        if not self._refresh_lock.acquire(False):
            # skip, if library is already loading
            logger.debug('Skip refresh playlist: library refresh is running.')
            return
        t0 = round(time.time())
        if 0 < self._refresh_library_rate \
             < self._refresh_threshold + t0 - self._refresh_last:
            # skip, upcoming library refresh
            logger.debug('Skip refresh playlist: ' +
                         'library refresh is around the corner')
            self._refresh_lock.release()
            return
        if self._refresh_last > t0 - self._refresh_threshold:
            # skip, library was just updated
            logger.debug('Skip refresh playlist: ' +
                         'library just finished')
            self._refresh_lock.release()
            return
        logger.info('Start refreshing Google Music playlists')
        self.playlists.refresh()
        t = round(time.time()) - t0
        logger.info('Finished refreshing Google Music content in %ds', t)
        self._refresh_lock.release()
