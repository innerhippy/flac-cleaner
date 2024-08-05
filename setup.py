from setuptools import setup
from flac_cleaner import __version__

with open("README.md", "rb") as f:
    long_descr = f.read().decode("utf-8")

setup(
    name="flac-cleaner",
    packages=["flac_cleaner"],
    install_requires=[
        'mutagen',
        'Click',
    ],
    entry_points={
        "console_scripts": ['flac-cleaner = flac_cleaner.cli:main']
    },
    version=__version__,
    description="Flac audio file cleanser.",
    long_description=long_descr,
    author="Will Hall",
    author_email="will@innerhippy.com",
    url="",
)
