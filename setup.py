import re
from setuptools import setup


version = re.search(
    r'^__version__\s*=\s*"(.*)"',
    open('flac_cleaner/__init__.py').read(),
    re.M
).group(1)

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
    version=version,
    description="Flac audio file cleanser.",
    long_description=long_descr,
    author="Will Hall",
    author_email="will@innerhippy.com",
    url="",
)
