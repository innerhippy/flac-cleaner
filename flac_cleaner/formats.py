from abc import ABC, abstractmethod
import re
import os
import logging
from mutagen.flac import FLAC
from mutagen.mp3 import MP3
from mutagen.easyid3 import EasyID3

LOG = logging.getLogger(__name__)


class AudioTags(ABC):
    REGEX = re.compile(r'^(\d+)[-. ]+(.*)\.(?:flac|mp3)$')
    ALL_TAGS = ['artist', 'album', 'title', 'tracknumber']
    PADDING = 2

    def __init__(self, path):
        self._tracknumber = None
        self._title = None
        match = self.REGEX.match(os.path.basename(path))
        if match:
            tracknumber, self._title = match.groups()
            self._tracknumber = f'{int(tracknumber):0{self.PADDING}d}'

    def verify(self, tags):
        if not self.PATH_REGEX.match(self.filename):
            LOG.error(f'Bad file format {self.path!r}')

        missing_tags = set(tags) - set(self.obj.keys())
        if missing_tags:
            LOG.error(
                f'Missing tags: {self.path}: {", ".join(missing_tags)!r}'
            )

    @abstractmethod
    def load(self, path):
        pass

    @property
    def title(self):
        return self._title

    @property
    def tracknumber(self):
        return self._tracknumber

    @property
    def tags(self):
        return self.obj.tags

    @property
    def path(self):
        return self.obj.filename

    @property
    def filename(self):
        return os.path.basename(self.obj.filename)

    @property
    def dirname(self):
        return os.path.dirname(self.obj.filename)

    @property
    def clean_filename(self):
        return f'{self._tracknumber} - {self._title}.{self.EXT}'

    @property
    def clean_path(self):
        return os.path.join(self.dirname, self.clean_filename)

    def __str__(self) -> str:
        return self.obj.pprint()

    def transform_tags(self, tags):
        return tags

    def set_tags(self, tags):
        self.obj.tags.update(self.transform_tags(tags))

    def save(self) -> None:
        self.obj.save()

    def rename(self, path):
        os.rename(self.path, path)
        self.obj = self.load(path)


class Flac(AudioTags):
    EXT = 'flac'
    PATH_REGEX = re.compile(r'^(\d+) - (.*)\.flac$')

    def __init__(self, path):
        super(Flac, self).__init__(path)
        self.obj = self.load(path)

    def load(self, path):
        return FLAC(os.path.abspath(path))

    def transform_tags(self, tags):
        return {k.upper(): v for k, v in tags.items()}

    @property
    def artist(self):
        return self.obj.tags['ARTIST'][0] if 'ARTIST' in self.tags else None

    @property
    def album(self):
        return self.obj.tags['ALBUM'][0] if 'ALBUM' in self.tags else None


class Mp3(AudioTags):
    EXT = 'mp3'
    PATH_REGEX = re.compile(r'^(\d+) - (.*)\.mp3$')

    def __init__(self, path):
        super(Mp3, self).__init__(path)
        self.obj = self.load(path)

    def load(self, path):
        return MP3(os.path.abspath(path), ID3=EasyID3)

    def clear(self):
        # Need to clear all ID3 tags and reload
        mp3 = MP3(self.path)
        mp3.clear()
        mp3.save()
        self.obj = MP3(self.path, ID3=EasyID3)
        self.obj.clear()

    @property
    def title(self):
        return f'{self._tracknumber} - {self._title}'

    @property
    def artist(self):
        return self.obj.tags['artist'][0] if 'artist' in self.tags else None

    @property
    def album(self):
        return self.obj.tags['album'][0] if 'album' in self.tags else None
