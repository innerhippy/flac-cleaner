import re
import os
import click
import logging


from flac_cleaner.formats import AudioTags, Mp3, Flac
from flac_cleaner import __version__

LOG = logging.getLogger(__name__)

CONTEXT_SETTINGS = {
    'help_option_names': ['-h', '--help'],
}


class PathFormatError(Exception):
    pass


def audio_file(path):
    if os.path.isfile(path):
        _, ext = os.path.splitext(path)
        if ext in ('.mp3', '.flac'):
            yield path


def discover_audio_files(path):
    if os.path.isfile(path):
        yield from audio_file(path)

    for root, _, paths in os.walk(path):
        for filename in paths:
            yield from audio_file(os.path.join(root, filename))


def object_from_path(path):
    if path.endswith('.flac'):
        return Flac(path)
    elif path.endswith('.mp3'):
        return Mp3(path)
    else:
        raise PathFormatError(f'Unsupported format {path!r}')


def discover_audio(path):
    for path in discover_audio_files(path):
        try:
            yield object_from_path(path)
        except PathFormatError:
            LOG.error(path)


@click.group(context_settings=CONTEXT_SETTINGS)
@click.option('-n', '--dry-run', is_flag=True, help="Dry run")
@click.version_option(version=__version__, message=f'v{__version__}')
@click.option('--padding', default=2, show_default=True, help="Track padding")
@click.pass_context
def main(ctx, dry_run, padding) -> None:
    """ Manage flac tags, inspect or fix.
    """
    noop = '[DRY RUN] ' if dry_run else ''
    formatter = f'%(asctime)s - %(levelname)s - {noop}%(message)s'
    logging.basicConfig(level=logging.INFO, format=formatter)
    ctx.obj = {'dry_run': dry_run}
    AudioTags.PADDING = padding


@main.command()
@click.argument('dir')
@click.option('-A', '--artist', help="Artist")
@click.option('-a', '--album', help="Album")
@click.option('-y', '--year', help="Year")
@click.option('-d', '--disc', help="Disc")
@click.option('-c', '--clear', is_flag=True, help="Clear all tags")
@click.pass_context
def clean(ctx, dir, artist, album, year, disc, clear):
    """ Ensure filename conforms to "<tracknumber> - <title>.flac"
        Adds tags where missing.
    """
    dry_run = ctx.obj['dry_run']

    tags = {}

    for obj in discover_audio(dir):

        if obj.clean_path != obj.path:
            LOG.info(f'Renaming {obj.path!r} to {obj.clean_path!r}')

            if not dry_run:
                obj.rename(obj.clean_path)

        tags['tracknumber'] = obj.tracknumber
        tags['title'] = obj.title

        if artist:
            tags['artist'] = artist
        else:
            assert obj.artist, f'{obj.path!r}: Artist not defined anywhere'
            tags['artist'] = obj.artist

        if album:
            tags['album'] = album
        elif obj.album:
            tags['album'] = obj.album

        if year:
            tags['date'] = year

        if disc:
            tags['discnumber'] = disc

        current_tags = {t: obj.tags.get(t)[0] for t in tags if t in obj.tags}

        if clear:
            obj.clear()

        obj.set_tags(tags)

        new_tags = {t: obj.tags.get(t)[0] for t in tags if t in obj.tags}

        added_tags = new_tags.keys() - current_tags.keys()
        if added_tags:
            LOG.info(f'New tags {", ".join(added_tags)!r}')

        removed_tags = current_tags.keys() - new_tags.keys()
        if removed_tags:
            LOG.info(f'Removed tags {", ".join(removed_tags)!r}')

        common_tags = current_tags.keys() & new_tags.keys()
        changed_tags = {
            f'{tag}: {current_tags[tag]!r} -> {new_tags[tag]!r}'
            for tag in common_tags if current_tags[tag] != new_tags[tag]
        }

        for tag in changed_tags:
            LOG.info(f'Changed tag {tag!r}')

        if not dry_run:
            obj.save()


@main.command()
@click.argument('dir')
@click.option(
    '-t',
    '--tags',
    multiple=True,
    default=AudioTags.ALL_TAGS,
    show_default=True, help="Expected tags"
)
@click.option('-f', '--full', is_flag=True, help="Full verify (inc tags)")
def verify(dir, tags, full):
    """ Scan a directory recursively and report filenames that
        do not conform or do not contain tags:
            - artist
            - album
            - title
            - tracknumber
    """
    regex = re.compile(r'^(\d+) - (.*)\.(flac|mp3)$')

    for path in discover_audio_files(dir):
        if not regex.match(os.path.basename(path)):
            LOG.error(f'Bad file path {path!r}')
            continue

        if full:
            obj = AudioTags.object_from_path(path)
            obj.verify(tags=tags)


@main.command()
@click.argument('dir')
def tags(dir):
    """ Print tags for each flac file discovered
    """
    for obj in discover_audio(dir):
        click.echo(obj)
