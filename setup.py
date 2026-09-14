from setuptools import setup

APP = ["archivex.py"]
DATA_FILES = []
OPTIONS = {
    "argv_emulation": False,
    "iconfile": "icon.icns",
    "includes": ["pyzipper", "py7zr"],
    "packages": ["tkinterdnd2"],
    "plist": {
        "CFBundleName": "Archivex",
        "CFBundleDisplayName": "Archivex",
        "CFBundleIdentifier": "com.archivex.app",
        "CFBundleVersion": "1.0.0",
        "CFBundleShortVersionString": "1.0.0",
        "NSHumanReadableCopyright": "2026",
        "LSMinimumSystemVersion": "10.15",
        "NSHighResolutionCapable": True,
        "NSAppleEventsUsageDescription":
            "Archivex opens archive files sent by Finder.",
        "CFBundleDocumentTypes": [
            {
                "CFBundleTypeName": "ZIP Archive",
                "CFBundleTypeExtensions": ["zip"],
                "CFBundleTypeRole": "Editor",
            },
            {
                "CFBundleTypeName": "TAR Archive",
                "CFBundleTypeExtensions": ["tar", "tgz", "tbz2", "txz"],
                "CFBundleTypeRole": "Editor",
            },
            {
                "CFBundleTypeName": "GZip TAR Archive",
                "CFBundleTypeExtensions": ["gz"],
                "CFBundleTypeRole": "Editor",
            },
            {
                "CFBundleTypeName": "7-Zip Archive",
                "CFBundleTypeExtensions": ["7z"],
                "CFBundleTypeRole": "Editor",
            },
        ],
    },
}

setup(
    app=APP,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
