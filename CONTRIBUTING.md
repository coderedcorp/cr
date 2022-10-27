Contributing
============

Environment Setup
-----------------

Create a virtual environment:

```
python -m venv ./.venv
(linux)   source ./.venv/scripts/activate
(windows) ./.venv/Scripts/Activate.ps1
```

First install the developer tools, then the package in editable mode:

```console
$ python -m pip install -r requirements-dev.txt
$ python -m pip install -e .
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

The Azure Pipeline will generate python packages (source dist, wheel) and binaries for Windows, macOS, and Linux.

To publish:
1. Download the dist artifact from the tagged release run of [the pipeline](https://dev.azure.com/coderedcorp/cr-github/_build?definitionId=17).
2. Sign them with our certificate (if applicable, see below).
3. Create a release on GitHub and upload the files.
4. Rename `cr-0.0.tar.gz` to `cr.tar.gz`, and upload all of the built files to crcloud-static under the `/www/cli/` folder.

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


Code Signing Certificate
------------------------

Certificate was purchased from: https://SignMyCode.com. Go here to renew, reissue, or revoke it.

### Windows

To sign the PyInstaller binaries on Windows, make sure the Windows SDK is installed (i.e. install [Visual Studio Build Tools](https://visualstudio.microsoft.com/downloads/#build-tools-for-visual-studio-2022) then select "Desktop Development with C++"). This is required to get [signtool](https://learn.microsoft.com/en-us/dotnet/framework/tools/signtool-exe)

First, convert the certificate + private key into a PFX file:

```
openssl pkcs12 -export -in .\CERTIFICATE.crt -inkey .\PRIVATE_KEY.pem -out codered.pfx
```

Next, open the "Developer PowerShell" or "Developer Command Prompt" and sign the binary using the PFX file and its password. When signing, also timestamp it using Sectigo's server.

```
signtool sign /f .\codered.pfx /p "password" /fd certHash /td certHash /tr "http://timestamp.sectigo.com" .\cr.exe
```

The `cr.exe` binary is now signed.

### macOS

Currently in the process of being verified by Apple.
