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
$ ruff format .
$ ruff check --fix .
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

The program is set up as a Python package, therefore, a source dist and wheel can be built. When building within a venv, the `--no-isolation` flag may be required:

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

Certificate was purchased from: https://SignMyCode.com (issued by Sectigo). Go here to renew, reissue, or revoke it.

### Windows

To sign the PyInstaller binaries on Windows, make sure the Windows SDK is installed (i.e. install [Visual Studio Build Tools](https://visualstudio.microsoft.com/downloads/#build-tools-for-visual-studio-2022) then select "Desktop Development with C++"). This is required to get [signtool](https://learn.microsoft.com/en-us/dotnet/framework/tools/signtool-exe)

First, convert the certificate + private key into a PFX file (with no password):

```
openssl pkcs12 -export -passout "pass:" -in .\CERTIFICATE.crt -inkey .\PRIVATE_KEY.pem -out CERTIFICATE.pfx
```

These certificate files are accessible in our private SharePoint, and the PFX is also stored as a secure file in the Azure Pipeline library.

Next, open the "Developer PowerShell" or "Developer Command Prompt" and sign the binary using the PFX file. When signing, also timestamp it using Sectigo's server.

```
signtool sign /f .\CERTIFICATE.pfx /fd certHash /td certHash /tr "http://timestamp.sectigo.com" .\cr.exe
```

The `cr.exe` binary is now signed and ready to be distributed.

### macOS

Binaries must be signed and notarized by Apple. A caveat to this process is that the PyInstaller build MUST be performed on the mac which has the code signing certificate installed, due to recent requirements by Apple about runtime hardening and signing of collected binaries (i.e. Python libs bundled with the app must also be signed before they are bundled). See: https://pyinstaller.org/en/latest/feature-notes.html#macos-binary-code-signing

The mac must have the FULL Xcode installed (not just the command line tools).

Finally, the CodeRed signing certificate, which was obtained from Apple, must be installed. This is accessible in our private SharePoint. Related Apple developer ID password etc. is available in our private Bitwarden. The certificate and passwords are also stored as secure files / variables in the Azure Pipeline library.

The normal command to sign a binary would be (e.g. if we had written this in Go or C):

```
codesign --sign "Developer ID Application: CodeRed LLC (26334S6DB6)" --timestamp --options runtime ./cr-macos
```

However due to intracacies of PyInstaller bundling, we must have PyInstaller sign each lib before it is bundled. To build a fully signed bundle, set the `CR_RELEASE=True` environment variable, and run PyInstaller on the mac with the certificate and Xcode installed:

```
pyinstaller ./cr.spec

mv ./dist/cr ./dist/cr-macos
```

How that you have a signed binary, it must be notarized by Apple. First, zip the file, then use `notarytool` to submit the file for notarization. The process takes about a minute.

```
ditto -c -k ./dist/cr-macos ./dist/cr-macos.zip

xcrun notarytool submit --apple-id BITWARDEN --password BITWARDEN --team-id BITWARDEN --wait ./dist/cr-macos.zip
```

If the status is anything other than "Accepted", you can see logs by copying the UUID provided in the output, and running this command:

```
xcrun notarytool log COPIED_ID --apple-id BITWARDEN --password BITWARDEN --team-id BITWARDEN
```

Upon success, the `./dist/cr-macos` binary is now ready to be uploaded and distributed. Test that the notarization worked by running the binary:

```
chmod +x ./dist/cr-macos
./dist/cr-macos --debug
```

#### Use in Azure Pipelines

To install the certificate in Azure Pipelines, a special `.p12` file containing the certificate + private key must be created. Follow these steps to generate the files.

1. Export the private key from the mac, name it `macos.key.p12`. This MUST have a password otherwise it breaks OpenSSL.

2. Download the certificate from Apple, it will be named `macos_DeveloperID_application.cer`.

Convert the certificate:

```
openssl x509 -in macos_DeveloperID_application.cer -inform DER -out macos_DeveloperID_application.cer.pem -outform PEM
```

Convert the private key:

```
openssl pkcs12 -nocerts -in macos.key.p12 -out mykey.key.pem
```

Generate the `p12` file. This MUST have a password to be imported on the mac. Enter the password to the private key when prompted, and re-use the same password for the outputted key.

```
openssl pkcs12 -export -in .\macos_developerID_application.cer.pem -inkey .\macos.key.pem -out .\macos_developerID_application.p12
```

Now upload the `macos_developerID_application.p12` as a secure file in the pipeline library.
