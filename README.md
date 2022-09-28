`cr` - CodeRed Cloud CLI
========================

The official CodeRed Cloud command line tool. Easily deploy your Django, Wagtail, WordPress, or Static HTML sites directly from your terminal or CI/CD pipeline!


Install
-------

Install from GitHub:

```
pip install https://github.com/coderedcorp/cr/archive/refs/heads/main.zip
```

NOTE: We have not yet uploaded to PyPI due to a naming collision. The following instruction is a DRAFT.

The easiest way to install `cr` is through `pip`:

```
pip install cr
```

Alternatively, you can download a published binary, to run `cr` without having Python installed on the system.


Usage
-----

To use `cr`, first log in to your CodeRed Cloud dashboard and create an API key for your Client here: https://app.codered.cloud/billing/

Tokens are secret and should be stored either in the `CR_TOKEN` environment variable, or in your personal config file at `~/.cr.ini` as so:

```ini
[cr]
token = a1b2c3...
```

The `webapp` handle is your website's CodeRed Cloud subdomain. For example: `example.codered.cloud` would be `example`.

Finally, run `cr --help` to see usage, or `cr {command} --help` to see usage about a particular command.


Contributing
------------

Create a virtual environment:

```
python -m venv ./.venv
(linux)   source ./.venv/scripts/activate
(windows) ./.venv/Scripts/Activate.ps1
```

Install the package, and developer tools:

```console
$ pip install -r requirements-dev.txt
```

Run the command line tool:

```console
$ cr --help
```

During development, lint your code with:

```console
$ flake8 .
$ mypy .
$ black .
```

Tests should be written in `tests/test_{name}.py` where name is the source file in `cr/` it is testing.

```console
$ pytest
```

Type annotations are used to enforce static typing. Read about [Mypy](http://mypy-lang.org/examples.html) and about [type annotations in Python3](https://www.python.org/dev/peps/pep-3107/).

For example:

```python
# Normal python.
def fun(name, age):
    return f"{0}, age: {1}"

# Annotated. Requires string, int; and returns a string.
def fun(name: str, age: int) -> str:
    return f"{0}, age: {1}"
```

Adding new functionality
------------------------

To keep the bundle small and portable, stick to the Python Standard Library as much as possible, and prefer pure-Python packages with few dependencies if a 3rd-party package must be used.

New functionality should be implemented in a `Command` subclass, and then invoked via the command-line interface using `argparse`.

All functionality must be fully cross-platform between Windows and Posix systems. This primarily means using ``pathlib.Path`` objects rather than hard-coding paths as strings, and using our established subprocess utilities in ``utils.py``.


Publishing
----------

The Azure Pipeline will generate python packages (source dist, wheel) and binaries for Mac (11 or newer), Windows (10 or newer), and Linux (Ubuntu 20.04 or newer). All binaries are for 64-bit systems.

### On PyPI

The program is set up as a Python package, therefore, a source dist and wheel can be built. When building within a venv, the `--no-isolation` flag is required:

```console
$ python -m build --no-isolation --outdir ./dist/pypi/
```

Then upload the contents using twine:
```console
$ twine ./dist/pypi/*
```

### Binaries

PyInstaller is used to create binaries. PyInstaller must be run on the same platform as the output binaries, i.e. it cannot cross-compile. Additionally, it should be run on the oldest supported version of the platform. For example, running on Windows 10 will generate a build that is compatible with Windows 10 and 11.

```console
$ pyinstaller --clean --dist ./dist/bin/ ./cr.spec
```

NOTE: macOS binaries will require special security permissions to run since we currently do not have code signing in place.

Upload files from `./dist/bin/` to GitHub under the release.
