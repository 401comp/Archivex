# Archivex

Create, extract, and encrypt ZIP, TAR, and 7z archives from a native macOS desktop app.

## Features

- Create and extract ZIP, TAR, and 7z archives
- Password-protect supported archive formats
- Browse for source and destination folders without using Terminal
- Progress reporting and cancellation for long-running operations

## Requirements

macOS 10.15 or later, plus Python with Tk support.

## Build

```bash
./build.sh
```

The build script creates an isolated environment, installs the dependencies from
`requirements.txt`, runs the self-test, and produces a standalone application.

## License

See the source distribution for license details.
