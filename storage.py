"""Small transactional local store; Windows DPAPI protects API credentials."""
import base64
import ctypes
import json
import os
from pathlib import Path
import sqlite3
import sys
import threading


def data_directory():
    override = os.environ.get("ETSY_STUDIO_DATA")
    if override:
        return Path(override)
    if os.name == "nt":
        return Path(os.environ["LOCALAPPDATA"]) / "EtsyListingStudio"
    return Path(__file__).parent / ".local"


class Store:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            self.directory.chmod(0o700)
        except OSError:
            pass
        self.path = self.directory / "studio.sqlite3"
        self.lock = threading.RLock()
        with self.connection() as db:
            db.execute("CREATE TABLE IF NOT EXISTS documents (kind TEXT, id TEXT, data TEXT NOT NULL, PRIMARY KEY(kind,id))")

    def connection(self):
        db = sqlite3.connect(str(self.path), timeout=20)
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def get(self, kind, key, default=None):
        with self.lock, self.connection() as db:
            row = db.execute("SELECT data FROM documents WHERE kind=? AND id=?", (kind, str(key))).fetchone()
            return json.loads(row[0]) if row else default

    def put(self, kind, key, value):
        with self.lock, self.connection() as db:
            db.execute("INSERT INTO documents VALUES (?,?,?) ON CONFLICT(kind,id) DO UPDATE SET data=excluded.data", (kind, str(key), json.dumps(value, ensure_ascii=False, allow_nan=False)))

    def delete(self, kind, key):
        with self.lock, self.connection() as db:
            db.execute("DELETE FROM documents WHERE kind=? AND id=?", (kind, str(key)))

    def all(self, kind):
        with self.lock, self.connection() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT data FROM documents WHERE kind=? ORDER BY rowid DESC", (kind,))]

    def replace_shop_listings(self, shop_id, listings, shop):
        # A failed sync never leaves a half-replaced listing cache.
        with self.lock, self.connection() as db:
            rows = list(db.execute("SELECT id,data FROM documents WHERE kind='listing'"))
            for key, data in rows:
                if str(json.loads(data)["shop_id"]) == str(shop_id):
                    db.execute("DELETE FROM documents WHERE kind='listing' AND id=?", (key,))
            for listing in listings:
                db.execute("INSERT INTO documents VALUES ('listing',?,?)", (str(listing["listing_id"]), json.dumps(listing, ensure_ascii=False)))
            db.execute("INSERT INTO documents VALUES ('shop',?,?) ON CONFLICT(kind,id) DO UPDATE SET data=excluded.data", (str(shop_id), json.dumps(shop)))

    def commit_shop_sync(self, shop_id, updates, removed_ids, shop):
        # Commit refreshed inventories, summary/status changes, confirmed removals
        # and the checkpoint together. Unchanged inventory documents stay intact.
        with self.lock, self.connection() as db:
            for key in removed_ids:
                db.execute("DELETE FROM documents WHERE kind='listing' AND id=?", (str(key),))
                db.execute("DELETE FROM documents WHERE kind='gallery' AND id=?", (str(key),))
            for listing in updates:
                if str(listing["shop_id"]) != str(shop_id):
                    raise ValueError("Sync returned a product from a different shop.")
                db.execute("INSERT INTO documents VALUES ('listing',?,?) ON CONFLICT(kind,id) DO UPDATE SET data=excluded.data", (str(listing["listing_id"]), json.dumps(listing, ensure_ascii=False)))
                db.execute("DELETE FROM documents WHERE kind='gallery' AND id=?", (str(listing["listing_id"]),))
            db.execute("INSERT INTO documents VALUES ('shop',?,?) ON CONFLICT(kind,id) DO UPDATE SET data=excluded.data", (str(shop_id), json.dumps(shop)))


class Vault:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def protect(data, decrypt=False):
        if os.name != "nt":
            raise ValueError("Secure Etsy connections are available on Windows. Sample mode works on this computer.")

        class Blob(ctypes.Structure):
            _fields_ = [("size", ctypes.c_ulong), ("data", ctypes.POINTER(ctypes.c_ubyte))]

        source_buffer = ctypes.create_string_buffer(data)
        source = Blob(len(data), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
        result = Blob()
        crypt = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        if decrypt:
            fn = crypt.CryptUnprotectData
        else:
            fn = crypt.CryptProtectData
        fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(Blob)]
        fn.restype = ctypes.c_int
        if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
            raise ValueError("Windows could not open the protected Etsy connection. Reconnect this shop.")
        try:
            return ctypes.string_at(result.data, result.size)
        finally:
            kernel.LocalFree(result.data)

    def get(self, key):
        if sys.platform == "darwin":
            raw = self.keychain(key)
            return json.loads(raw) if raw else None
        value = self.store.get("vault", key)
        return json.loads(self.protect(base64.b64decode(value), True)) if value else None

    def put(self, key, value):
        if sys.platform == "darwin":
            self.keychain(key, json.dumps(value).encode())
            return
        encrypted = self.protect(json.dumps(value).encode())
        self.store.put("vault", key, base64.b64encode(encrypted).decode())

    def delete(self, key):
        if sys.platform == "darwin":
            self.keychain(key, delete=True)
            return
        self.store.delete("vault", key)

    @staticmethod
    def keychain(key, value=None, delete=False):
        # Native Keychain APIs keep secrets out of command-line arguments/files.
        lib = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
        cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        find = lib.SecKeychainFindGenericPassword
        find.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_uint32, ctypes.c_char_p, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p)]
        find.restype = ctypes.c_int32
        service, account = b"EtsyListingStudio", key.encode()
        length, content, item = ctypes.c_uint32(), ctypes.c_void_p(), ctypes.c_void_p()
        status = find(None, len(service), service, len(account), account, ctypes.byref(length), ctypes.byref(content), ctypes.byref(item))
        raw = None
        try:
            if status == 0:
                raw = ctypes.string_at(content, length.value)
                free = lib.SecKeychainItemFreeContent
                free.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
                free(None, content)
            elif status != -25300:
                raise ValueError("macOS Keychain did not allow access. Allow this app's connection storage and try again.")
            if delete:
                if item.value:
                    lib.SecKeychainItemDelete.argtypes = [ctypes.c_void_p]
                    lib.SecKeychainItemDelete(item)
            elif value is not None:
                if item.value:
                    modify = lib.SecKeychainItemModifyAttributesAndData
                    modify.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]
                    status = modify(item, None, len(value), value)
                else:
                    add = lib.SecKeychainAddGenericPassword
                    add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p]
                    status = add(None, len(service), service, len(account), account, len(value), value, None)
                if status:
                    raise ValueError("macOS Keychain could not save the Etsy connection.")
            return raw
        finally:
            if item.value:
                cf.CFRelease(item)
