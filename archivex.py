#!/usr/bin/env python3
"""Archivex - create, extract, and encrypt archives."""

import os
import sys
import json
import sqlite3
import threading
import tempfile
import shutil
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import tarfile
import zipfile
from pathlib import Path
from datetime import datetime

try:
    import pyzipper
    HAS_PYZIPPER = True
except ImportError:
    HAS_PYZIPPER = False

try:
    import py7zr
    HAS_7Z = True
except ImportError:
    HAS_7Z = False

try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    HAS_DND = True
except ImportError:
    HAS_DND = False

APP_NAME = "Archivex"
APP_VERSION = "1.0.1"

FORMATS_CREATE = [
    ("ZIP", ".zip"),
    ("TAR", ".tar"),
    ("TAR.GZ", ".tar.gz"),
    ("TAR.BZ2", ".tar.bz2"),
    ("TAR.XZ", ".tar.xz"),
]
if HAS_7Z:
    FORMATS_CREATE.append(("7Z", ".7z"))

FORMATS_EXTRACT = [".zip", ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz"]
if HAS_7Z:
    FORMATS_EXTRACT.append(".7z")

ENCRYPTABLE = {".zip", ".7z"}

COMPRESSION_LEVELS = [
    ("Store (no compression)", 0),
    ("Fast", 1),
    ("Normal", 6),
    ("Best", 9),
]

OVERWRITE_MODES = [
    ("Overwrite existing", "replace"),
    ("Skip existing", "skip"),
    ("Rename new file", "rename"),
]


def get_data_dir():
    if getattr(sys, "frozen", False):
        d = os.path.expanduser("~/Library/Application Support/Archivex")
    else:
        d = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(d, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------

class Preferences:
    def __init__(self):
        self.path = os.path.join(get_data_dir(), "prefs.json")
        self.data = {
            "window_x": None, "window_y": None,
            "window_w": 760, "window_h": 660,
            "last_dir": os.path.expanduser("~"),
        }
        self._load()

    def _load(self):
        try:
            with open(self.path) as f:
                self.data.update(json.load(f))
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def save(self):
        with open(self.path, "w") as f:
            json.dump(self.data, f, indent=2)

    def __getitem__(self, key):
        return self.data.get(key)

    def __setitem__(self, key, val):
        self.data[key] = val


# ---------------------------------------------------------------------------
# Database (operation history)
# ---------------------------------------------------------------------------

class Database:
    def __init__(self):
        self.conn = sqlite3.connect(os.path.join(get_data_dir(), "archivex.db"))
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                operation TEXT NOT NULL,
                archive_path TEXT NOT NULL,
                format TEXT NOT NULL,
                file_count INTEGER DEFAULT 0,
                total_size INTEGER DEFAULT 0,
                encrypted INTEGER DEFAULT 0,
                timestamp TEXT NOT NULL
            )
        """)
        self.conn.commit()

    def add(self, operation, archive_path, fmt, file_count, total_size, encrypted):
        self.conn.execute(
            "INSERT INTO history "
            "(operation, archive_path, format, file_count, total_size, encrypted, timestamp) "
            "VALUES (?,?,?,?,?,?,?)",
            (operation, archive_path, fmt, file_count, total_size,
             int(encrypted), datetime.now().isoformat()),
        )
        self.conn.commit()

    def recent(self, limit=50):
        cur = self.conn.execute(
            "SELECT operation, archive_path, format, file_count, "
            "total_size, encrypted, timestamp "
            "FROM history ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return cur.fetchall()

    def close(self):
        self.conn.close()


# ---------------------------------------------------------------------------
# Archive engine
# ---------------------------------------------------------------------------

class ArchiveEngine:

    @staticmethod
    def _collect_files(source, include_hidden=False):
        source = os.path.abspath(source)
        files = []
        if os.path.isfile(source):
            files.append((source, os.path.basename(source)))
        elif os.path.isdir(source):
            base = os.path.basename(source)
            for root, dirs, names in os.walk(source):
                if not include_hidden:
                    dirs[:] = [d for d in dirs if not d.startswith(".")]
                for name in names:
                    if not include_hidden and name.startswith("."):
                        continue
                    full = os.path.join(root, name)
                    arc = os.path.join(base, os.path.relpath(full, source))
                    files.append((full, arc))
        return files

    @staticmethod
    def _resolve_conflict(target, mode):
        """mode: 'replace' | 'skip' | 'rename'.
        Returns (path_to_write_or_None, skipped_bool)."""
        if not os.path.exists(target):
            return target, False
        if mode == "replace":
            return target, False
        if mode == "skip":
            return None, True
        # rename: append (2), (3), … before the extension
        base, ext = os.path.splitext(target)
        # For .tar.gz etc, splitext only strips last component; that's fine.
        i = 2
        while True:
            candidate = f"{base} ({i}){ext}"
            if not os.path.exists(candidate):
                return candidate, False
            i += 1

    # -- create ---------------------------------------------------------------

    @staticmethod
    def create_zip(source, output, password=None, cb=None,
                   level=6, include_hidden=False):
        files = ArchiveEngine._collect_files(source, include_hidden)
        total = len(files)
        if level == 0:
            comp_kw = {"compression": zipfile.ZIP_STORED}
        else:
            comp_kw = {"compression": zipfile.ZIP_DEFLATED,
                       "compresslevel": level}
        if password:
            if not HAS_PYZIPPER:
                raise RuntimeError(
                    "pyzipper is required for encrypted ZIP.\n"
                    "Install it:  pip install pyzipper"
                )
            with pyzipper.AESZipFile(
                output, "w",
                compression=comp_kw["compression"],
                encryption=pyzipper.WZ_AES,
            ) as zf:
                zf.setpassword(password.encode("utf-8"))
                for i, (fp, an) in enumerate(files):
                    zf.write(fp, an)
                    if cb:
                        cb(i + 1, total)
        else:
            with zipfile.ZipFile(output, "w", **comp_kw) as zf:
                for i, (fp, an) in enumerate(files):
                    zf.write(fp, an)
                    if cb:
                        cb(i + 1, total)
        return total

    @staticmethod
    def create_tar(source, output, mode="w", password=None, cb=None,
                   level=6, include_hidden=False):
        if password:
            raise RuntimeError(
                "TAR archives don't support encryption.\n"
                "Use ZIP or 7Z for password-protected archives."
            )
        files = ArchiveEngine._collect_files(source, include_hidden)
        total = len(files)

        # tarfile only accepts compresslevel for gz/bz2; xz uses preset.
        # Level 0 (Store) → fall back to fastest available for compressed
        # variants, since gzip/bzip2/xz can't produce uncompressed output.
        open_kw = {}
        if mode in ("w:gz", "w:bz2"):
            open_kw["compresslevel"] = max(1, level)
        elif mode == "w:xz":
            open_kw["preset"] = max(1, level)

        with tarfile.open(output, mode, **open_kw) as tf:
            for i, (fp, an) in enumerate(files):
                tf.add(fp, an, recursive=False)
                if cb:
                    cb(i + 1, total)
        return total

    @staticmethod
    def create_7z(source, output, password=None, cb=None,
                  level=6, include_hidden=False, header_encrypt=False):
        if not HAS_7Z:
            raise RuntimeError(
                "py7zr is required for 7Z support.\n"
                "Install it:  pip install py7zr"
            )
        files = ArchiveEngine._collect_files(source, include_hidden)
        total = len(files)

        kw = {}
        if password:
            kw["password"] = password
            if header_encrypt:
                kw["header_encryption"] = True
        if level == 0:
            kw["filters"] = [{"id": py7zr.FILTER_COPY}]
        else:
            kw["filters"] = [{"id": py7zr.FILTER_LZMA2, "preset": level}]

        with py7zr.SevenZipFile(output, "w", **kw) as zf:
            for i, (fp, an) in enumerate(files):
                zf.write(fp, an)
                if cb:
                    cb(i + 1, total)
        return total

    # -- extract --------------------------------------------------------------

    @staticmethod
    def _write_from_stream(fh, out_path):
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "wb") as w:
            shutil.copyfileobj(fh, w)

    @staticmethod
    def extract_zip(archive, output, password=None, cb=None,
                    overwrite="replace"):
        try:
            def _iter(zf, pwd):
                members = zf.namelist()
                total = len(members)
                extracted = 0
                for i, name in enumerate(members):
                    info = zf.getinfo(name)
                    if info.is_dir():
                        os.makedirs(os.path.join(output, name), exist_ok=True)
                    else:
                        target = os.path.join(output, name)
                        write_to, skipped = ArchiveEngine._resolve_conflict(
                            target, overwrite
                        )
                        if skipped:
                            if cb:
                                cb(i + 1, total)
                            continue
                        os.makedirs(os.path.dirname(write_to) or ".",
                                    exist_ok=True)
                        with zf.open(info, pwd=pwd) as src, \
                                open(write_to, "wb") as dst:
                            shutil.copyfileobj(src, dst)
                        extracted += 1
                    if cb:
                        cb(i + 1, total)
                return extracted

            if password and HAS_PYZIPPER:
                with pyzipper.AESZipFile(archive, "r") as zf:
                    zf.setpassword(password.encode("utf-8"))
                    return _iter(zf, None)

            with zipfile.ZipFile(archive, "r") as zf:
                pwd = password.encode("utf-8") if password else None
                return _iter(zf, pwd)
        except RuntimeError as exc:
            low = str(exc).lower()
            if "password" in low or "bad password" in low or "encrypted" in low:
                raise RuntimeError("Wrong password, or the archive requires one.")
            raise

    @staticmethod
    def extract_tar(archive, output, password=None, cb=None,
                    overwrite="replace"):
        if password:
            raise RuntimeError("TAR archives don't support passwords.")
        with tarfile.open(archive, "r:*") as tf:
            safe = []
            for m in tf.getmembers():
                if m.name.startswith("/") or ".." in m.name.split("/"):
                    continue
                safe.append(m)
            total = len(safe)
            extracted = 0
            for i, member in enumerate(safe):
                target = os.path.join(output, member.name)
                if member.isdir():
                    os.makedirs(target, exist_ok=True)
                elif member.isreg():
                    write_to, skipped = ArchiveEngine._resolve_conflict(
                        target, overwrite
                    )
                    if skipped:
                        if cb:
                            cb(i + 1, total)
                        continue
                    os.makedirs(os.path.dirname(write_to) or ".",
                                exist_ok=True)
                    src = tf.extractfile(member)
                    if src is not None:
                        with open(write_to, "wb") as dst:
                            shutil.copyfileobj(src, dst)
                        extracted += 1
                else:
                    # symlinks, hardlinks, etc. — let tarfile handle safely
                    try:
                        tf.extract(member, output, filter="data")
                    except TypeError:
                        tf.extract(member, output)
                if cb:
                    cb(i + 1, total)
            return extracted

    @staticmethod
    def extract_7z(archive, output, password=None, cb=None,
                   overwrite="replace"):
        if not HAS_7Z:
            raise RuntimeError(
                "py7zr is required for 7Z support.\n"
                "Install it:  pip install py7zr"
            )
        kw = {}
        if password:
            kw["password"] = password

        # For overwrite modes other than 'replace', pre-filter names and
        # extract only those that would land on non-existing paths.
        if overwrite == "replace":
            with py7zr.SevenZipFile(archive, "r", **kw) as zf:
                names = zf.getnames()
                zf.extractall(output)
                if cb:
                    cb(len(names), len(names))
                return len(names)

        with py7zr.SevenZipFile(archive, "r", **kw) as zf:
            names = zf.getnames()

        if overwrite == "skip":
            targets = [n for n in names
                       if not os.path.exists(os.path.join(output, n))]
        else:  # rename
            targets = names  # extract to a scratch dir then move with renames

        if not targets:
            if cb:
                cb(len(names), len(names))
            return 0

        if overwrite == "skip":
            with py7zr.SevenZipFile(archive, "r", **kw) as zf:
                zf.extract(path=output, targets=targets)
            if cb:
                cb(len(names), len(names))
            return len(targets)

        # rename: extract to a temp dir, then move each file with conflict
        # resolution.
        scratch = tempfile.mkdtemp(prefix="archivex_7z_")
        try:
            with py7zr.SevenZipFile(archive, "r", **kw) as zf:
                zf.extractall(scratch)
            moved = 0
            for name in names:
                src = os.path.join(scratch, name)
                if not os.path.isfile(src):
                    continue
                target = os.path.join(output, name)
                write_to, skipped = ArchiveEngine._resolve_conflict(
                    target, overwrite
                )
                if skipped:
                    continue
                os.makedirs(os.path.dirname(write_to) or ".", exist_ok=True)
                shutil.move(src, write_to)
                moved += 1
            if cb:
                cb(len(names), len(names))
            return moved
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    # -- helpers --------------------------------------------------------------

    @staticmethod
    def archive_stem(path):
        name = os.path.basename(path)
        for ext in (".tar.gz", ".tar.bz2", ".tar.xz"):
            if name.lower().endswith(ext):
                return name[: -len(ext)]
        for ext in (".tgz", ".tbz2", ".txz"):
            if name.lower().endswith(ext):
                return name[: -len(ext)]
        return os.path.splitext(name)[0]

    @staticmethod
    def detect_format(filepath):
        lower = filepath.lower()
        for ext in (".tar.gz", ".tar.bz2", ".tar.xz"):
            if lower.endswith(ext):
                return ext
        alias = {".tgz": ".tar.gz", ".tbz2": ".tar.bz2", ".txz": ".tar.xz"}
        for a, real in alias.items():
            if lower.endswith(a):
                return real
        for ext in (".zip", ".7z", ".tar"):
            if lower.endswith(ext):
                return ext
        return None

    @staticmethod
    def needs_password(filepath):
        fmt = ArchiveEngine.detect_format(filepath)
        if fmt == ".zip":
            try:
                with zipfile.ZipFile(filepath, "r") as zf:
                    for info in zf.infolist():
                        if info.flag_bits & 0x1:
                            return True
            except Exception:
                pass
            if HAS_PYZIPPER:
                try:
                    with pyzipper.AESZipFile(filepath, "r") as zf:
                        for info in zf.infolist():
                            if info.flag_bits & 0x1:
                                return True
                except Exception:
                    pass
        elif fmt == ".7z" and HAS_7Z:
            try:
                with py7zr.SevenZipFile(filepath, "r") as zf:
                    if zf.needs_password():
                        return True
            except Exception:
                return True
        return False


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class ArchivexApp:
    def __init__(self):
        if HAS_DND:
            self.root = TkinterDnD.Tk()
        else:
            self.root = tk.Tk()
        self.root.title(f"{APP_NAME} v{APP_VERSION}")
        self.root.minsize(760, 660)

        self.prefs = Preferences()
        self.db = Database()
        self.working = False

        self.style = ttk.Style()
        if sys.platform == "darwin":
            self.style.theme_use("aqua")
        else:
            self.style.theme_use("clam")

        self._build_ui()
        self._center_window()
        self._setup_dnd()
        self._setup_file_open_hooks()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # deferred so the UI is fully drawn before opening a file
        self.root.after(150, self._handle_launch_args)

    # -- geometry -------------------------------------------------------------

    def _center_window(self):
        w = self.prefs["window_w"] or 760
        h = self.prefs["window_h"] or 660
        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = (sw - w) // 2
        y = (sh - h) // 2
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    # -- UI construction ------------------------------------------------------

    def _build_ui(self):
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=(8, 0))

        self._build_create_tab()
        self._build_extract_tab()
        self._build_history_tab()

        bar = ttk.Frame(self.root)
        bar.pack(fill=tk.X, padx=8, pady=6)
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(bar, textvariable=self.status_var, anchor=tk.W).pack(
            side=tk.LEFT, fill=tk.X, expand=True
        )
        self.progress = ttk.Progressbar(bar, length=220, mode="determinate")
        self.progress.pack(side=tk.RIGHT, padx=(8, 0))

    # -- Create tab -----------------------------------------------------------

    def _build_create_tab(self):
        tab = ttk.Frame(self.notebook, padding=14)
        self.notebook.add(tab, text="  Create Archive  ")
        tab.columnconfigure(0, weight=1)

        # source
        ttk.Label(tab, text="Source (file or folder):").grid(
            row=0, column=0, sticky=tk.W, pady=(0, 2)
        )
        sf = ttk.Frame(tab)
        sf.grid(row=1, column=0, sticky=tk.EW, pady=(0, 10))
        sf.columnconfigure(0, weight=1)
        self.c_source = tk.StringVar()
        self.c_source_entry = ttk.Entry(sf, textvariable=self.c_source)
        self.c_source_entry.grid(row=0, column=0, sticky=tk.EW, padx=(0, 4))
        ttk.Button(sf, text="File…", command=self._browse_c_file).grid(
            row=0, column=1, padx=(0, 4)
        )
        ttk.Button(sf, text="Folder…", command=self._browse_c_folder).grid(
            row=0, column=2
        )

        # format
        ttk.Label(tab, text="Archive format:").grid(
            row=2, column=0, sticky=tk.W, pady=(0, 2)
        )
        ff = ttk.Frame(tab)
        ff.grid(row=3, column=0, sticky=tk.W, pady=(0, 10))
        self.c_format = tk.StringVar(value=FORMATS_CREATE[0][0])
        combo = ttk.Combobox(
            ff, textvariable=self.c_format,
            values=[f[0] for f in FORMATS_CREATE],
            state="readonly", width=16,
        )
        combo.grid(row=0, column=0)
        combo.bind("<<ComboboxSelected>>", self._on_fmt_change)
        self.enc_label = ttk.Label(ff, text="  Encryption available", foreground="green")
        self.enc_label.grid(row=0, column=1, padx=(8, 0))

        # output
        ttk.Label(tab, text="Save archive as:").grid(
            row=4, column=0, sticky=tk.W, pady=(0, 2)
        )
        of = ttk.Frame(tab)
        of.grid(row=5, column=0, sticky=tk.EW, pady=(0, 10))
        of.columnconfigure(0, weight=1)
        self.c_output = tk.StringVar()
        ttk.Entry(of, textvariable=self.c_output).grid(
            row=0, column=0, sticky=tk.EW, padx=(0, 4)
        )
        ttk.Button(of, text="Browse…", command=self._browse_c_output).grid(
            row=0, column=1
        )

        # Options group
        opt = ttk.LabelFrame(tab, text="Options", padding=8)
        opt.grid(row=6, column=0, sticky=tk.EW, pady=(0, 8))
        opt.columnconfigure(1, weight=1)

        ttk.Label(opt, text="Compression:").grid(
            row=0, column=0, sticky=tk.W, padx=(0, 8)
        )
        self.c_level = tk.StringVar(value="Normal")
        ttk.Combobox(
            opt, textvariable=self.c_level,
            values=[n for n, _ in COMPRESSION_LEVELS],
            state="readonly", width=22,
        ).grid(row=0, column=1, sticky=tk.W)

        self.c_include_hidden = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            opt, text="Include hidden files (dotfiles)",
            variable=self.c_include_hidden,
        ).grid(row=1, column=0, columnspan=2, sticky=tk.W, pady=(4, 0))

        self.c_delete_source = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            opt, text="Delete source after successful archive",
            variable=self.c_delete_source,
        ).grid(row=2, column=0, columnspan=2, sticky=tk.W, pady=(2, 0))

        self.c_reveal = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            opt, text="Reveal archive in Finder when done",
            variable=self.c_reveal,
        ).grid(row=3, column=0, columnspan=2, sticky=tk.W, pady=(2, 0))

        # password toggle
        self.c_use_pw = tk.BooleanVar()
        ttk.Checkbutton(
            tab, text="Password protect", variable=self.c_use_pw,
            command=self._toggle_c_pw,
        ).grid(row=7, column=0, sticky=tk.W, pady=(0, 4))

        self.c_pw_frame = ttk.LabelFrame(tab, text="Encryption (AES-256)", padding=8)
        self.c_pw_frame.grid(row=8, column=0, sticky=tk.EW, pady=(0, 10))
        self.c_pw_frame.columnconfigure(1, weight=1)
        ttk.Label(self.c_pw_frame, text="Password:").grid(
            row=0, column=0, sticky=tk.W, padx=(0, 8)
        )
        self.c_pw = tk.StringVar()
        self.c_pw_entry = ttk.Entry(self.c_pw_frame, textvariable=self.c_pw, show="•")
        self.c_pw_entry.grid(row=0, column=1, sticky=tk.EW, padx=(0, 4))
        self.c_show_pw = tk.BooleanVar()
        ttk.Checkbutton(
            self.c_pw_frame, text="Show", variable=self.c_show_pw,
            command=lambda: self.c_pw_entry.configure(
                show="" if self.c_show_pw.get() else "•"
            ),
        ).grid(row=0, column=2)
        ttk.Label(self.c_pw_frame, text="Confirm:").grid(
            row=1, column=0, sticky=tk.W, padx=(0, 8), pady=(6, 0)
        )
        self.c_pw2 = tk.StringVar()
        self.c_pw2_entry = ttk.Entry(
            self.c_pw_frame, textvariable=self.c_pw2, show="•"
        )
        self.c_pw2_entry.grid(row=1, column=1, sticky=tk.EW, padx=(0, 4), pady=(6, 0))
        self.c_header_encrypt = tk.BooleanVar(value=False)
        self.c_header_cb = ttk.Checkbutton(
            self.c_pw_frame,
            text="Encrypt filenames too (7Z only — hides file list)",
            variable=self.c_header_encrypt,
        )
        self.c_header_cb.grid(row=2, column=0, columnspan=3, sticky=tk.W, pady=(6, 0))
        self.c_pw_frame.grid_remove()

        # create button
        self.c_btn = ttk.Button(tab, text="Create Archive", command=self._do_create)
        self.c_btn.grid(row=9, column=0, pady=(4, 0))

    # -- Extract tab ----------------------------------------------------------

    def _build_extract_tab(self):
        tab = ttk.Frame(self.notebook, padding=14)
        self.notebook.add(tab, text="  Extract Archive  ")
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)

        # file list header + buttons
        hdr = ttk.Frame(tab)
        hdr.grid(row=0, column=0, sticky=tk.EW, pady=(0, 2))
        ttk.Label(hdr, text="Archives to extract:").pack(side=tk.LEFT)
        ttk.Button(hdr, text="Remove", command=self._remove_e_selected).pack(
            side=tk.RIGHT, padx=(4, 0)
        )
        ttk.Button(hdr, text="Add…", command=self._browse_e_source).pack(
            side=tk.RIGHT
        )

        # file listbox
        lf = ttk.Frame(tab)
        lf.grid(row=1, column=0, sticky=tk.NSEW, pady=(0, 6))
        lf.columnconfigure(0, weight=1)
        lf.rowconfigure(0, weight=1)
        self.e_files = []
        self.e_listbox = tk.Listbox(lf, selectmode=tk.EXTENDED, height=6)
        self.e_listbox.grid(row=0, column=0, sticky=tk.NSEW)
        e_vsb = ttk.Scrollbar(lf, orient=tk.VERTICAL, command=self.e_listbox.yview)
        self.e_listbox.configure(yscrollcommand=e_vsb.set)
        e_vsb.grid(row=0, column=1, sticky=tk.NS)

        self.e_info = tk.StringVar()
        ttk.Label(tab, textvariable=self.e_info, foreground="gray").grid(
            row=2, column=0, sticky=tk.W, pady=(0, 6)
        )

        # output dir
        ttk.Label(tab, text="Extract to:").grid(
            row=3, column=0, sticky=tk.W, pady=(0, 2)
        )
        ef = ttk.Frame(tab)
        ef.grid(row=4, column=0, sticky=tk.EW, pady=(0, 8))
        ef.columnconfigure(0, weight=1)
        self.e_output = tk.StringVar()
        ttk.Entry(ef, textvariable=self.e_output).grid(
            row=0, column=0, sticky=tk.EW, padx=(0, 4)
        )
        ttk.Button(ef, text="Browse…", command=self._browse_e_output).grid(
            row=0, column=1
        )

        # password
        self.e_pw_frame = ttk.LabelFrame(tab, text="Password (if required)", padding=8)
        self.e_pw_frame.grid(row=5, column=0, sticky=tk.EW, pady=(0, 8))
        self.e_pw_frame.columnconfigure(1, weight=1)
        ttk.Label(self.e_pw_frame, text="Password:").grid(
            row=0, column=0, sticky=tk.W, padx=(0, 8)
        )
        self.e_pw = tk.StringVar()
        self.e_pw_entry = ttk.Entry(
            self.e_pw_frame, textvariable=self.e_pw, show="•"
        )
        self.e_pw_entry.grid(row=0, column=1, sticky=tk.EW, padx=(0, 4))
        self.e_show_pw = tk.BooleanVar()
        ttk.Checkbutton(
            self.e_pw_frame, text="Show", variable=self.e_show_pw,
            command=lambda: self.e_pw_entry.configure(
                show="" if self.e_show_pw.get() else "•"
            ),
        ).grid(row=0, column=2)
        self.e_pw_all = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            self.e_pw_frame, text="Apply to all", variable=self.e_pw_all,
        ).grid(row=1, column=1, sticky=tk.W, pady=(4, 0))

        # Extract options
        eopt = ttk.LabelFrame(tab, text="Options", padding=8)
        eopt.grid(row=6, column=0, sticky=tk.EW, pady=(0, 8))
        eopt.columnconfigure(1, weight=1)

        self.e_subfolder = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            eopt, text="Extract each archive into its own subfolder",
            variable=self.e_subfolder,
        ).grid(row=0, column=0, columnspan=2, sticky=tk.W)

        ttk.Label(eopt, text="If file exists:").grid(
            row=1, column=0, sticky=tk.W, padx=(0, 8), pady=(4, 0)
        )
        self.e_overwrite = tk.StringVar(value="Overwrite existing")
        ttk.Combobox(
            eopt, textvariable=self.e_overwrite,
            values=[n for n, _ in OVERWRITE_MODES],
            state="readonly", width=22,
        ).grid(row=1, column=1, sticky=tk.W, pady=(4, 0))

        self.e_open_dest = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            eopt, text="Open destination in Finder when done",
            variable=self.e_open_dest,
        ).grid(row=2, column=0, columnspan=2, sticky=tk.W, pady=(2, 0))

        self.e_delete_archive = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            eopt, text="Delete archive after successful extraction",
            variable=self.e_delete_archive,
        ).grid(row=3, column=0, columnspan=2, sticky=tk.W, pady=(2, 0))

        self.e_btn = ttk.Button(tab, text="Extract All", command=self._do_extract)
        self.e_btn.grid(row=7, column=0, pady=(4, 0))

    # -- History tab ----------------------------------------------------------

    def _build_history_tab(self):
        tab = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(tab, text="  History  ")

        cols = ("operation", "archive", "format", "files", "size", "encrypted", "date")
        self.hist = ttk.Treeview(tab, columns=cols, show="headings", height=16)
        for c, w in [
            ("operation", 65), ("archive", 230), ("format", 75),
            ("files", 50), ("size", 85), ("encrypted", 72), ("date", 145),
        ]:
            self.hist.heading(c, text=c.capitalize())
            self.hist.column(c, width=w, minwidth=40)

        vsb = ttk.Scrollbar(tab, orient=tk.VERTICAL, command=self.hist.yview)
        self.hist.configure(yscrollcommand=vsb.set)
        self.hist.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        self._refresh_history()

    def _refresh_history(self):
        for row in self.hist.get_children():
            self.hist.delete(row)
        for op, path, fmt, fc, sz, enc, ts in self.db.recent():
            self.hist.insert("", tk.END, values=(
                op, os.path.basename(path), fmt, fc,
                self._fmt_size(sz), "Yes" if enc else "No",
                ts[:19].replace("T", " "),
            ))

    # -- browse callbacks -----------------------------------------------------

    def _browse_c_file(self):
        p = filedialog.askopenfilename(title="Select file to archive")
        if p:
            self.c_source.set(p)
            self._suggest_output()

    def _browse_c_folder(self):
        p = filedialog.askdirectory(title="Select folder to archive")
        if p:
            self.c_source.set(p)
            self._suggest_output()

    def _browse_c_output(self):
        ext = dict(FORMATS_CREATE).get(self.c_format.get(), ".zip")
        p = filedialog.asksaveasfilename(
            title="Save archive as", defaultextension=ext,
            filetypes=[(self.c_format.get(), f"*{ext}"), ("All files", "*.*")],
        )
        if p:
            self.c_output.set(p)

    def _browse_e_source(self):
        exts = " ".join(f"*{e}" for e in FORMATS_EXTRACT)
        paths = filedialog.askopenfilenames(
            title="Select archives",
            filetypes=[("Archives", exts), ("All", "*.*")],
        )
        if paths:
            for p in paths:
                if p not in self.e_files:
                    self.e_files.append(p)
            self._refresh_e_listbox()

    def _remove_e_selected(self):
        sel = list(self.e_listbox.curselection())
        if not sel:
            return
        for idx in reversed(sel):
            del self.e_files[idx]
        self._refresh_e_listbox()

    def _refresh_e_listbox(self):
        self.e_listbox.delete(0, tk.END)
        pw_needed = 0
        for p in self.e_files:
            name = os.path.basename(p)
            fmt = ArchiveEngine.detect_format(p) or "?"
            sz = self._fmt_size(os.path.getsize(p)) if os.path.isfile(p) else "?"
            lock = ""
            if ArchiveEngine.needs_password(p):
                lock = "  [locked]"
                pw_needed += 1
            self.e_listbox.insert(tk.END, f"{name}  ({fmt.upper()}, {sz}){lock}")
        count = len(self.e_files)
        info = f"{count} archive{'s' if count != 1 else ''} selected"
        if pw_needed:
            info += f"  —  {pw_needed} need a password"
        self.e_info.set(info if count else "")
        if self.e_files and not self.e_output.get():
            self.e_output.set(os.path.dirname(self.e_files[0]))

    def _browse_e_output(self):
        p = filedialog.askdirectory(title="Extract to")
        if p:
            self.e_output.set(p)

    def _suggest_output(self):
        src = self.c_source.get()
        if not src:
            return
        ext = dict(FORMATS_CREATE).get(self.c_format.get(), ".zip")
        base = os.path.basename(src.rstrip("/"))
        if os.path.isfile(src):
            base = os.path.splitext(base)[0]
        self.c_output.set(os.path.join(os.path.dirname(src), base + ext))

    # -- format / password toggles --------------------------------------------

    def _on_fmt_change(self, _event=None):
        ext = dict(FORMATS_CREATE).get(self.c_format.get(), ".zip")
        if ext in ENCRYPTABLE:
            self.enc_label.configure(text="  Encryption available", foreground="green")
        else:
            self.enc_label.configure(
                text="  No encryption for this format", foreground="gray"
            )
            self.c_use_pw.set(False)
            self.c_pw_frame.grid_remove()

        # header encryption is a 7z-only feature
        state = "normal" if ext == ".7z" else "disabled"
        try:
            self.c_header_cb.configure(state=state)
        except AttributeError:
            pass
        if ext != ".7z":
            self.c_header_encrypt.set(False)

        out = self.c_output.get()
        if out:
            for _, e in FORMATS_CREATE:
                if out.endswith(e):
                    out = out[: -len(e)]
                    break
            self.c_output.set(out + ext)

    def _toggle_c_pw(self):
        if self.c_use_pw.get():
            ext = dict(FORMATS_CREATE).get(self.c_format.get(), ".zip")
            if ext not in ENCRYPTABLE:
                messagebox.showwarning(
                    "Not supported",
                    f"{self.c_format.get()} doesn't support encryption.\n"
                    "Use ZIP or 7Z for password-protected archives.",
                )
                self.c_use_pw.set(False)
                return
            self.c_pw_frame.grid()
        else:
            self.c_pw_frame.grid_remove()
            self.c_pw.set("")
            self.c_pw2.set("")

    # -- drag-and-drop + open-with ------------------------------------------

    def _setup_dnd(self):
        if not HAS_DND:
            return
        try:
            self.c_source_entry.drop_target_register(DND_FILES)
            self.c_source_entry.dnd_bind("<<Drop>>", self._on_drop_create)
            self.e_listbox.drop_target_register(DND_FILES)
            self.e_listbox.dnd_bind("<<Drop>>", self._on_drop_extract)
        except Exception:
            pass

    def _parse_dnd(self, data):
        try:
            paths = self.root.tk.splitlist(data)
        except Exception:
            paths = [data]
        cleaned = []
        for p in paths:
            p = p.strip()
            if p.startswith("{") and p.endswith("}"):
                p = p[1:-1]
            if p:
                cleaned.append(p)
        return cleaned

    def _on_drop_create(self, event):
        paths = self._parse_dnd(event.data)
        if paths:
            self.c_source.set(paths[0])
            self._suggest_output()
            self.notebook.select(0)

    def _on_drop_extract(self, event):
        paths = self._parse_dnd(event.data)
        self._add_extract_files(paths)

    def _setup_file_open_hooks(self):
        # macOS Apple Event — fires when a file is opened via Finder/Open With
        # whether the app is launching or already running.
        if sys.platform == "darwin":
            try:
                self.root.createcommand(
                    "::tk::mac::OpenDocument", self._mac_open_docs
                )
            except Exception:
                pass

    def _mac_open_docs(self, *paths):
        self._add_extract_files(list(paths))

    def _handle_launch_args(self):
        # Files passed via command line (or py2app argv_emulation on launch).
        args = [a for a in sys.argv[1:] if not a.startswith("-")]
        self._add_extract_files(args)

    def _add_extract_files(self, paths):
        added = 0
        for p in paths:
            if not p or not os.path.isfile(p):
                continue
            if ArchiveEngine.detect_format(p) is None:
                continue
            if p not in self.e_files:
                self.e_files.append(p)
                added += 1
        if added:
            self._refresh_e_listbox()
            self.notebook.select(1)
            if not self.e_output.get():
                self.e_output.set(os.path.dirname(self.e_files[0]))
            self.status_var.set(
                f"Loaded {added} archive{'s' if added != 1 else ''} for extraction"
            )

    def _extract_one(self, src, out, pw, cb, overwrite="replace"):
        fmt = ArchiveEngine.detect_format(src)
        if not fmt:
            raise RuntimeError(f"Unsupported format: {os.path.basename(src)}")
        if fmt == ".zip":
            return ArchiveEngine.extract_zip(src, out, pw, cb, overwrite)
        if fmt in (".tar", ".tar.gz", ".tar.bz2", ".tar.xz"):
            return ArchiveEngine.extract_tar(src, out, pw, cb, overwrite)
        if fmt == ".7z":
            return ArchiveEngine.extract_7z(src, out, pw, cb, overwrite)
        raise RuntimeError(f"Unknown format: {fmt}")

    # -- Finder + completion helpers -----------------------------------------

    def _reveal_in_finder(self, path):
        if sys.platform != "darwin":
            return
        try:
            import subprocess
            subprocess.Popen(["open", "-R", path])
        except Exception:
            pass

    def _open_in_finder(self, path):
        if sys.platform != "darwin":
            return
        try:
            import subprocess
            subprocess.Popen(["open", path])
        except Exception:
            pass

    def _play_done_sound(self, ok=True):
        """Fire the system 'Glass' (success) or 'Basso' (error) sound on macOS."""
        if sys.platform != "darwin":
            return
        try:
            import subprocess
            snd = "Glass.aiff" if ok else "Basso.aiff"
            subprocess.Popen(
                ["afplay", f"/System/Library/Sounds/{snd}"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass

    def _bring_to_front(self):
        """Force the app window forward so the completion dialog is visible."""
        try:
            self.root.lift()
            self.root.attributes("-topmost", True)
            self.root.after(200, lambda: self.root.attributes("-topmost", False))
            self.root.focus_force()
        except Exception:
            pass

    # -- create ---------------------------------------------------------------

    def _do_create(self):
        if self.working:
            return
        src = self.c_source.get().strip()
        out = self.c_output.get().strip()
        fmt_name = self.c_format.get()
        ext = dict(FORMATS_CREATE).get(fmt_name, ".zip")
        use_pw = self.c_use_pw.get()
        pw = self.c_pw.get() if use_pw else None
        level = dict(COMPRESSION_LEVELS).get(self.c_level.get(), 6)
        include_hidden = self.c_include_hidden.get()
        header_enc = self.c_header_encrypt.get() and ext == ".7z"
        delete_source = self.c_delete_source.get()
        reveal = self.c_reveal.get()

        if not src or not os.path.exists(src):
            messagebox.showerror("Error", "Select a valid source file or folder.")
            return
        if not out:
            messagebox.showerror("Error", "Specify an output path.")
            return
        if use_pw:
            if not pw:
                messagebox.showerror("Error", "Enter a password.")
                return
            if pw != self.c_pw2.get():
                messagebox.showerror("Error", "Passwords don't match.")
                return
            if len(pw) < 4:
                messagebox.showwarning(
                    "Weak password", "Use at least 4 characters."
                )
                return

        self._set_working(True, self.c_btn)
        self.status_var.set(f"Creating {fmt_name} archive…")

        def cb(cur, tot):
            pct = cur / tot * 100 if tot else 100
            self.root.after(
                0, lambda: (
                    self.progress.configure(value=pct),
                    self.status_var.set(f"Archiving: {cur}/{tot} files"),
                )
            )

        def task():
            try:
                if ext == ".zip":
                    n = ArchiveEngine.create_zip(
                        src, out, pw, cb, level, include_hidden
                    )
                elif ext == ".tar":
                    n = ArchiveEngine.create_tar(
                        src, out, "w", pw, cb, level, include_hidden
                    )
                elif ext == ".tar.gz":
                    n = ArchiveEngine.create_tar(
                        src, out, "w:gz", pw, cb, level, include_hidden
                    )
                elif ext == ".tar.bz2":
                    n = ArchiveEngine.create_tar(
                        src, out, "w:bz2", pw, cb, level, include_hidden
                    )
                elif ext == ".tar.xz":
                    n = ArchiveEngine.create_tar(
                        src, out, "w:xz", pw, cb, level, include_hidden
                    )
                elif ext == ".7z":
                    n = ArchiveEngine.create_7z(
                        src, out, pw, cb, level, include_hidden, header_enc
                    )
                else:
                    raise RuntimeError(f"Unknown format: {ext}")

                sz = os.path.getsize(out) if os.path.exists(out) else 0
                self.db.add("Create", out, fmt_name, n, sz, bool(pw))

                if delete_source:
                    try:
                        if os.path.isdir(src):
                            shutil.rmtree(src)
                        else:
                            os.remove(src)
                    except Exception:
                        pass

                self.root.after(
                    0, lambda: self._create_ok(out, n, sz, bool(pw), reveal)
                )
            except Exception as exc:
                self.root.after(0, lambda: self._op_error(str(exc), self.c_btn))

        threading.Thread(target=task, daemon=True).start()

    def _create_ok(self, path, count, size, encrypted, reveal):
        self._set_working(False, self.c_btn)
        enc = " (encrypted)" if encrypted else ""
        self.progress["value"] = 100
        self.status_var.set(f"{count} files processed. Complete!")
        self._refresh_history()
        self._play_done_sound(ok=True)
        self._bring_to_front()
        messagebox.showinfo(
            "Archive Complete",
            f"Archive created successfully.\n\n"
            f"Files archived: {count}\n"
            f"Size: {self._fmt_size(size)}\n"
            f"Encrypted: {'Yes' if encrypted else 'No'}\n\n"
            f"Location:\n{path}",
        )
        if reveal:
            self._reveal_in_finder(path)

    # -- extract --------------------------------------------------------------

    def _do_extract(self):
        if self.working:
            return
        if not self.e_files:
            messagebox.showerror("Error", "Add at least one archive.")
            return
        out = self.e_output.get().strip()
        if not out:
            messagebox.showerror("Error", "Specify an output directory.")
            return
        pw_global = self.e_pw.get() or None
        apply_all = self.e_pw_all.get()
        subfolder = self.e_subfolder.get()
        overwrite = dict(OVERWRITE_MODES).get(
            self.e_overwrite.get(), "replace"
        )
        open_dest = self.e_open_dest.get()
        delete_archive = self.e_delete_archive.get()

        os.makedirs(out, exist_ok=True)
        self._set_working(True, self.e_btn)
        archives = list(self.e_files)
        total_archives = len(archives)

        def task():
            ok_count = 0
            total_items = 0
            errors = []
            last_dest = out

            for idx, src in enumerate(archives):
                name = os.path.basename(src)
                self.root.after(
                    0, lambda i=idx, n=name: (
                        self.status_var.set(
                            f"Extracting {i + 1}/{total_archives}: {n}"
                        ),
                        self.progress.configure(value=i / total_archives * 100),
                    ),
                )

                pw = pw_global if apply_all else None
                if not apply_all and ArchiveEngine.needs_password(src):
                    pw = pw_global

                dest = out
                if subfolder:
                    dest = os.path.join(out, ArchiveEngine.archive_stem(src))
                    os.makedirs(dest, exist_ok=True)

                fmt = ArchiveEngine.detect_format(src)
                try:
                    n = self._extract_one(src, dest, pw, None, overwrite)
                    self.db.add(
                        "Extract", src, fmt or "?", n,
                        os.path.getsize(src), bool(pw),
                    )
                    ok_count += 1
                    total_items += n
                    last_dest = dest
                    if delete_archive:
                        try:
                            os.remove(src)
                        except Exception:
                            pass
                except Exception as exc:
                    errors.append(f"{name}: {exc}")

            self.root.after(
                0, lambda: self._extract_batch_done(
                    last_dest, total_archives, ok_count, total_items,
                    errors, open_dest,
                ),
            )

        threading.Thread(target=task, daemon=True).start()

    def _extract_batch_done(self, path, total, ok, items, errors, open_dest):
        self._set_working(False, self.e_btn)
        self.progress["value"] = 100
        self._refresh_history()

        # clear the queue on full success
        if not errors and ok == total:
            self.e_files.clear()
            self._refresh_e_listbox()

        all_ok = not errors
        self._play_done_sound(ok=all_ok)
        self._bring_to_front()

        if all_ok:
            self.status_var.set(f"{items} files processed. Complete!")
            messagebox.showinfo(
                "Extraction Complete",
                f"All {ok} archive{'s' if ok != 1 else ''} extracted "
                f"successfully.\n\n"
                f"Total items: {items}\n\n"
                f"Location:\n{path}",
            )
        else:
            self.status_var.set(
                f"Finished — {ok}/{total} succeeded, {len(errors)} failed"
            )
            detail = "\n".join(errors[:10])
            if ok > 0:
                messagebox.showwarning(
                    "Extraction Finished — With Errors",
                    f"{ok} of {total} archive"
                    f"{'s' if total != 1 else ''} extracted.\n"
                    f"{len(errors)} failed.\n\n"
                    f"Errors:\n{detail}",
                )
            else:
                messagebox.showerror(
                    "Extraction Failed",
                    f"None of the {total} archive"
                    f"{'s' if total != 1 else ''} could be extracted.\n\n"
                    f"Errors:\n{detail}",
                )

        # open Finder AFTER the user dismisses the dialog so the message
        # isn't hidden behind Finder
        if open_dest and ok > 0:
            self._open_in_finder(path)

    # -- shared helpers -------------------------------------------------------

    def _set_working(self, busy, btn):
        self.working = busy
        btn.configure(state="disabled" if busy else "normal")
        if busy:
            self.progress["value"] = 0

    def _op_error(self, msg, btn):
        self._set_working(False, btn)
        self.progress["value"] = 0
        self.status_var.set("Error — see dialog for details")
        self._play_done_sound(ok=False)
        self._bring_to_front()
        messagebox.showerror("Error", msg)

    @staticmethod
    def _fmt_size(size):
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"

    def _on_close(self):
        try:
            geo = self.root.geometry()
            parts = geo.replace("x", "+").split("+")
            self.prefs["window_w"] = int(parts[0])
            self.prefs["window_h"] = int(parts[1])
            self.prefs["window_x"] = int(parts[2])
            self.prefs["window_y"] = int(parts[3])
            self.prefs.save()
        except Exception:
            pass
        self.db.close()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


# ---------------------------------------------------------------------------
# Self-test (headless)
# ---------------------------------------------------------------------------

def self_test():
    print(f"{APP_NAME} v{APP_VERSION} — self-test")
    tmp = tempfile.mkdtemp(prefix="archivex_test_")
    try:
        src = os.path.join(tmp, "data")
        os.makedirs(src)
        for i in range(5):
            with open(os.path.join(src, f"file{i}.txt"), "w") as f:
                f.write(f"content-{i}\n" * 50)
        # a hidden file for the include_hidden test
        with open(os.path.join(src, ".hidden.txt"), "w") as f:
            f.write("secret")

        passed = total = 0

        def check(label, cond, err=""):
            nonlocal passed, total
            total += 1
            if cond:
                print(f"  PASS  {label}")
                passed += 1
            else:
                print(f"  FAIL  {label}: {err}")

        # --- round-trip for every format ------------------------------------
        cases = [
            ("ZIP", ".zip", None, False),
            ("ZIP+AES", ".zip", None, True),
            ("TAR", ".tar", "w", False),
            ("TAR.GZ", ".tar.gz", "w:gz", False),
            ("TAR.BZ2", ".tar.bz2", "w:bz2", False),
            ("TAR.XZ", ".tar.xz", "w:xz", False),
        ]
        if HAS_7Z:
            cases += [("7Z", ".7z", None, False), ("7Z+AES", ".7z", None, True)]

        for label, ext, mode, use_pw in cases:
            arc = os.path.join(tmp, f"test{ext}")
            out = os.path.join(tmp, f"out_{label}")
            os.makedirs(out, exist_ok=True)
            pw = "Test!Pass9" if use_pw else None
            try:
                if ext == ".zip":
                    ArchiveEngine.create_zip(src, arc, pw)
                    ArchiveEngine.extract_zip(arc, out, pw)
                elif ext == ".7z":
                    ArchiveEngine.create_7z(src, arc, pw)
                    ArchiveEngine.extract_7z(arc, out, pw)
                else:
                    ArchiveEngine.create_tar(src, arc, mode)
                    ArchiveEngine.extract_tar(arc, out)
                ok = all(
                    os.path.exists(os.path.join(out, "data", f"file{i}.txt"))
                    for i in range(5)
                )
                check(label, ok, "extracted files missing")
            except Exception as exc:
                check(label, False, str(exc))

        # --- compression level: Store vs Best on ZIP ------------------------
        try:
            store_arc = os.path.join(tmp, "store.zip")
            best_arc = os.path.join(tmp, "best.zip")
            ArchiveEngine.create_zip(src, store_arc, level=0)
            ArchiveEngine.create_zip(src, best_arc, level=9)
            check(
                "Level Store>Best size",
                os.path.getsize(store_arc) > os.path.getsize(best_arc),
                "compressed archive should be smaller than stored",
            )
        except Exception as exc:
            check("Level Store>Best size", False, str(exc))

        # --- include_hidden ------------------------------------------------
        try:
            hid_arc = os.path.join(tmp, "hidden.zip")
            hid_out = os.path.join(tmp, "out_hidden")
            os.makedirs(hid_out, exist_ok=True)
            ArchiveEngine.create_zip(src, hid_arc, include_hidden=True)
            ArchiveEngine.extract_zip(hid_arc, hid_out)
            check(
                "Include hidden",
                os.path.exists(os.path.join(hid_out, "data", ".hidden.txt")),
                ".hidden.txt not included",
            )
        except Exception as exc:
            check("Include hidden", False, str(exc))

        # --- overwrite modes: skip / rename --------------------------------
        try:
            arc = os.path.join(tmp, "ow.zip")
            ow_out = os.path.join(tmp, "out_ow")
            os.makedirs(ow_out, exist_ok=True)
            ArchiveEngine.create_zip(src, arc)
            # first extract writes the files
            ArchiveEngine.extract_zip(arc, ow_out)
            marker = os.path.join(ow_out, "data", "file0.txt")
            with open(marker, "w") as f:
                f.write("MODIFIED")
            # skip should leave the modification alone
            ArchiveEngine.extract_zip(arc, ow_out, overwrite="skip")
            check(
                "Overwrite=skip preserves",
                open(marker).read() == "MODIFIED",
                "skip mode overwrote the file",
            )
            # rename should create a "(2)" sibling and leave the original
            ArchiveEngine.extract_zip(arc, ow_out, overwrite="rename")
            renamed = os.path.join(ow_out, "data", "file0 (2).txt")
            check(
                "Overwrite=rename creates sibling",
                os.path.exists(renamed) and open(marker).read() == "MODIFIED",
                f"renamed file missing at {renamed}",
            )
        except Exception as exc:
            check("Overwrite modes", False, str(exc))

        # --- 7z header encryption (filenames hidden without password) -----
        if HAS_7Z:
            try:
                arc = os.path.join(tmp, "hdr.7z")
                pw = os.environ.get("ARCHIVEX_TEST_PASSWORD", "test-password")
                ArchiveEngine.create_7z(
                    src, arc, password=pw, header_encrypt=True
                )
                # Reading without password should fail (headers encrypted)
                threw = False
                try:
                    with py7zr.SevenZipFile(arc, "r") as zf:
                        zf.getnames()
                except Exception:
                    threw = True
                check("7Z header encryption blocks listing", threw)
            except Exception as exc:
                check("7Z header encryption", False, str(exc))

        # --- archive_stem helper --------------------------------------------
        check(
            "archive_stem tar.gz",
            ArchiveEngine.archive_stem("foo/bar.tar.gz") == "bar",
        )
        check(
            "archive_stem zip",
            ArchiveEngine.archive_stem("baz.zip") == "baz",
        )

        print(f"\n{passed}/{total} passed")
        return passed == total
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------

def main():
    if "--self-test" in sys.argv:
        ok = self_test()
        sys.exit(0 if ok else 1)
    app = ArchivexApp()
    app.run()


if __name__ == "__main__":
    main()
