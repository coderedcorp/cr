from setuptools import setup
from cr import __version__


setup(
    name="cr",
    version=__version__,
    packages=["cr"],
    license="Proprietary",
    description="The official CodeRed Cloud command line tool.",
    author="CodeRed LLC",
    requires_python=">=3.7",
    install_requires=[
        "paramiko==2.11.*",
        "rich==12.5.*",
    ],
    classifiers=[
        "Environment :: Console",
    ],
    entry_points={
        "console_scripts": [
            "cr=cr.cli:main",
        ]
    },
)
