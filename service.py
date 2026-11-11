"""Etsy transport, cached shops, durable reviews and explicitly approved writes."""
import base64
import copy
import hashlib
import json
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from engine import changed_dependencies, listing_fields, LISTING_FIELDS, ReviewError, fingerprint, partial_snapshot, preview, snapshot
from storage import Vault

API = "https://api.etsy.com/v3"


class TaskBusy(ReviewError):
    def __init__(self, task):
        self.task = task or "Another shop operation"
        super().__init__("A shop operation is still running. Your saved work is preserved.")


class EtsyError(ReviewError):
    def __init__(self, message, status=None):
        self.status = status
        super().__init__(message)


def request_json(url, method="GET", data=None, headers=None, form=False):
    headers = dict(headers or {})
    if isinstance(data, bytes):
        payload = data
    elif data is not None:
        if form:
            pairs = []
            for k, v in data.items():
                if isinstance(v, list):
                    pairs.append((k, ",".join(str(x) for x in v)))
                elif isinstance(v, bool):
                    pairs.append((k, "true" if v else "false"))
                else:
                    pairs.append((k, "" if v is None else str(v)))
            payload = urllib.parse.urlencode(pairs).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            payload = json.dumps(data, allow_nan=False).encode()
            headers["Content-Type"] = "application/json"
    else:
        payload = None
    request = urllib.request.Request(url, data=payload, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read(32 * 1024 * 1024)
            return (json.loads(raw) if raw else {}), dict(response.headers)
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            retry = exc.headers.get("retry-after", "60")
            raise EtsyError("Etsy's request limit was reached. Wait " + retry[:20] + " seconds before syncing or retrying.")
        if exc.code in (401, 403):
            raise EtsyError("Etsy did not allow this request. Reconnect the shop and check the app's approved access.", exc.code)
        # Return API validation detail without echoing tokens/headers or request bodies.
        try:
            error = json.loads(exc.read(4096))
            message = str(error.get("error", ""))[:350]
        except (ValueError, AttributeError):
            message = ""
        raise EtsyError("Etsy could not complete the request (" + str(exc.code) + "). " + message, exc.code)
    except (urllib.error.URLError, TimeoutError, OSError):
        raise EtsyError("Etsy could not be reached. A submitted update may still have completed. Check its status before retrying.")


class Etsy:
    def __init__(self, service, shop_id):
        self.service, self.shop_id = service, str(shop_id)
        self.next_request = 0

    def request(self, path, method="GET", data=None, form=False, content_type=None):
        self.last_request_timing = {"wait": 0, "http": 0}
        account = self.service.vault.get("account:" + self.shop_id)
        if not account:
            raise EtsyError("Reconnect this shop to continue.")
        if account["expires_at"] < time.time() + 60:
            token, _ = request_json(API + "/public/oauth/token", "POST", {"grant_type": "refresh_token", "client_id": account["key"], "refresh_token": account["refresh_token"]}, form=True)
            account.update(token)
            account["expires_at"] = time.time() + token.get("expires_in", 3600)
            self.service.vault.put("account:" + self.shop_id, account)
        delay = self.next_request - time.monotonic()
        if delay > 0:
            waiting = time.perf_counter()
            time.sleep(delay)
            self.last_request_timing["wait"] = time.perf_counter() - waiting
        self.next_request = time.monotonic() + 0.55
        request_headers = {"x-api-key": account["key"] + ":" + account["secret"], "Authorization": "Bearer " + account["access_token"]}
        if content_type:
            request_headers["Content-Type"] = content_type
        started = time.perf_counter()
        try:
            result, headers = request_json(API + "/application" + path, method, data, request_headers, form)
        finally:
            self.last_request_timing["http"] = time.perf_counter() - started
        # Respect the actual allocated quota, rather than docs' example numbers.
        limit = headers.get("x-limit-per-second") or headers.get("X-Limit-Per-Second")
        if limit:
            try:
                self.next_request = time.monotonic() + max(0.55, 1.05 / max(1, float(limit)))
            except ValueError:
                pass
        return result

    def current(self, listing_id, observe=None):
        read = observe or (lambda step, fn: fn())
        listing = read("Read product from Etsy", lambda: self.request("/listings/" + str(listing_id)))
        if str(listing.get("shop_id")) != self.shop_id:
            raise ReviewError("This product belongs to a different shop.")
        inventory = read("Read variations from Etsy", lambda: self.request("/listings/" + str(listing_id) + "/inventory?max_variations_supported=3"))
        return listing, snapshot(listing, inventory)


class Service:
    def __init__(self, store, callback, live_enabled=False, vault=None):
        self.store, self.callback, self.live_enabled = store, callback, live_enabled
        self.vault = vault or Vault(store)
        from image_edits import ImageEdits
        self.images = ImageEdits(self)
        self.operation = threading.Lock()
        self.status_lock = threading.RLock()
        self.status = {"busy": False, "message": "", "revision": 0, "error": ""}
        self.pending_auth = {}
        self.stop = threading.Event()
        # Never automatically resume a write after a process interruption.
        for batch in store.all("batch"):
            if batch["status"] == "applying":
                batch["status"] = "interrupted"
                store.put("batch", batch["id"], batch)

    def touch(self):
        with self.status_lock:
            self.status["revision"] += 1

    def begin_task(self, message):
        with self.status_lock:
            if not self.operation.acquire(False):
                raise TaskBusy(self.status["message"])
            self.status.update(busy=True, message=message, error="", started_at=time.time(), progress=None, sync_progress=None)
            self.status["revision"] += 1

    def end_task(self):
        with self.status_lock:
            self.status.update(busy=False, message="", started_at=None, progress=None, sync_progress=None)
            self.status["revision"] += 1
            self.operation.release()

    def background(self, message, fn):
        self.begin_task(message)

        def run():
            try:
                fn()
            except Exception as exc:
                with self.status_lock:
                    self.status["error"] = str(exc) if isinstance(exc, (ReviewError, ValueError)) else "The task stopped unexpectedly. Your saved review is preserved."
            finally:
                self.end_task()
        try:
            threading.Thread(target=run, daemon=True).start()
        except Exception:
            self.end_task()
            raise

    def state(self):
        shops = self.store.all("shop")
        batches = [{k: b.get(k) for k in ("id", "shop_id", "shop_name", "created_at", "status", "label", "sample", "restore_of")} | {"counts": self.counts(b)} for b in self.store.all("batch")]
        return {"shops": shops, "listings": self.store.all("listing"), "batches": batches, "presets": self.store.all("preset"), "matches": self.store.all("match"), "status": self.status, "live_enabled": self.live_enabled, "callback": self.callback, "sync_minutes": self.store.get("setting", "sync_minutes", 15)}

    @staticmethod
    def counts(batch):
        result = {}
        for item in batch["items"]:
            result[item["status"]] = result.get(item["status"], 0) + 1
        return result

    def shop(self, shop_id):
        shop = self.store.get("shop", shop_id)
        if not shop:
            raise ReviewError("Choose a connected shop.")
        return shop

    def save_credentials(self, data):
        if not self.live_enabled:
            raise ReviewError("Live connections need Windows secure storage and local HTTPS. Sample mode is ready to use.")
        key, secret = str(data.get("key", "")).strip(), str(data.get("secret", "")).strip()
        if not key or not secret or ":" in key or len(key) > 200 or len(secret) > 200:
            raise ReviewError("Enter the API key and shared secret from your Etsy app registration.")
        connection_id = str(uuid.uuid4())
        self.vault.put("app:" + connection_id, {"key": key, "secret": secret})
        return {"connection_id": connection_id}

    def auth_start(self, connection_id):
        if not self.live_enabled:
            raise ReviewError("Use the Windows HTTPS launcher to connect Etsy.")
        app = self.vault.get("app:" + str(connection_id))
        if not app:
            raise ReviewError("Set up the Etsy connection first.")
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        self.pending_auth = {k: v for k, v in self.pending_auth.items() if v["expires"] > time.time()}
        self.pending_auth[state] = {"app": app, "verifier": verifier, "expires": time.time() + 600, "connection_id": connection_id}
        params = {"response_type": "code", "client_id": app["key"], "redirect_uri": self.callback, "scope": "shops_r listings_r listings_w", "state": state, "code_challenge": challenge, "code_challenge_method": "S256"}
        return {"url": "https://www.etsy.com/oauth/connect?" + urllib.parse.urlencode(params)}

    def auth_finish(self, query):
        state = query.get("state", [""])[0]
        pending = self.pending_auth.pop(state, None)
        if not pending or pending["expires"] < time.time():
            raise ReviewError("This connection request expired. Start Connect with Etsy again.")
        if query.get("error") or not query.get("code"):
            raise ReviewError("The connection was not approved. You can try again when ready.")
        app = pending["app"]
        token, _ = request_json(API + "/public/oauth/token", "POST", {"grant_type": "authorization_code", "client_id": app["key"], "code": query["code"][0], "redirect_uri": self.callback, "code_verifier": pending["verifier"]}, form=True)
        granted = set(token.get("scope", "shops_r listings_r listings_w").split())
        if not {"shops_r", "listings_r", "listings_w"}.issubset(granted):
            raise ReviewError("Etsy did not grant the permissions needed to read and edit this shop.")
        headers = {"x-api-key": app["key"] + ":" + app["secret"], "Authorization": "Bearer " + token["access_token"]}
        me, _ = request_json(API + "/application/users/me", headers=headers)
        shop, _ = request_json(API + "/application/users/" + str(int(me["user_id"])) + "/shops", headers=headers)
        shop_id = str(shop["shop_id"])
        if me.get("shop_id") and str(me["shop_id"]) != shop_id:
            raise ReviewError("Etsy returned an unexpected shop. Please reconnect.")
        account = {**app, **token, "expires_at": time.time() + token.get("expires_in", 3600)}
        self.vault.put("account:" + shop_id, account)
        self.vault.delete("app:" + pending["connection_id"])
        self.store.put("shop", shop_id, {"shop_id": shop_id, "name": shop["shop_name"], "currency": shop.get("currency_code", "USD"), "icon": shop.get("icon_url_fullxfull"), "sample": False, "last_sync": None, "sections": []})
        self.touch()
        return shop_id

    def sync_shop(self, shop_id, full=False):
        shop = self.shop(shop_id)
        started = time.time()
        cached = {str(l["listing_id"]): l for l in self.store.all("listing") if str(l["shop_id"]) == str(shop_id)}
        full = bool(full or not shop.get("last_sync") or not cached)
        mode = "full" if full else "changes"
        stats = {"mode": mode, "checked": 0, "refreshed": 0, "reused": 0, "status_changes": 0, "removed": 0, "summary_requests": 0, "inventory_requests": 0, "inventories_checked": 0}

        def report(step, product="", total=None):
            with self.status_lock:
                self.status["message"] = "Full shop sync…" if full else "Checking for changed products…"
                self.status["sync_progress"] = {"mode": mode, "step": step, "product": product, "checked": stats["checked"], "updated": stats["inventories_checked"], "total_updates": total}
                self.status["revision"] += 1

        report("Checking listing summaries")
        if shop.get("sample"):
            stats.update(checked=len(cached), reused=len(cached), elapsed=time.time()-started, finished_at=time.time())
            shop.update(last_sync=time.time(), sync_stats=stats, last_sync_started=started)
            if full:
                shop["last_full_sync"] = shop["last_sync"]
            self.store.put("shop", shop_id, shop)
            return
        api = Etsy(self, shop_id)
        taxonomy = self.store.get("reference", "taxonomy", {})
        if not taxonomy or time.time() - taxonomy.get("checked", 0) > 86400:
            tree = api.request("/seller-taxonomy/nodes").get("results", [])
            names = {}
            def collect(nodes, parents):
                for node in nodes:
                    path = parents + [node["name"]]
                    names[str(node["id"])] = " / ".join(path)
                    collect(node.get("children", []), path)
            collect(tree, [])
            taxonomy = {"checked": time.time(), "names": names}
            self.store.put("reference", "taxonomy", taxonomy)
        sections = api.request("/shops/" + str(shop_id) + "/sections").get("results", [])
        summaries, duplicates = {}, set()
        for state in ("active", "draft", "inactive", "expired", "sold_out"):
            offset = 0
            while True:
                if self.stop.is_set():
                    raise ReviewError("Sync paused because the application is closing.")
                page = api.request("/shops/" + str(shop_id) + "/listings?" + urllib.parse.urlencode({"state": state, "limit": 100, "offset": offset, "includes": "Images"}))
                stats["summary_requests"] += 1
                rows, count = page.get("results"), page.get("count")
                if not isinstance(rows, list) or not isinstance(count, int) or count < 0 or (not rows and offset < count):
                    raise ReviewError("Etsy returned an incomplete listing page. The previous sync is preserved; try again.")
                for listing in rows:
                    lid = str(listing["listing_id"])
                    if str(listing.get("shop_id")) != str(shop_id):
                        raise ReviewError("Sync returned a product from a different shop.")
                    if lid in summaries:
                        duplicates.add(lid)
                    summaries[lid] = listing
                stats["checked"] = len(summaries)
                report("Checking listing summaries")
                offset += len(rows)
                if offset >= count:
                    break
        removed = []
        # A listing can move between status pages while they are being read.
        # Reconcile missing/duplicate IDs directly; only 404/410 confirms removal.
        for lid in sorted((set(cached) - set(summaries)) | duplicates):
            if self.stop.is_set():
                raise ReviewError("Sync paused because the application is closing.")
            report("Checking a product’s current status", cached.get(lid, {}).get("title", ""))
            try:
                listing = api.request("/listings/" + lid + "?includes=Images")
            except EtsyError as exc:
                if exc.status not in (404, 410):
                    raise
                summaries.pop(lid, None)
                removed.append(lid)
                continue
            if str(listing.get("shop_id")) != str(shop_id):
                raise ReviewError("Sync returned a product from a different shop.")
            summaries[lid] = listing
        # Listing timestamps do not document an inventory-wide revision contract.
        # Read inventories directly in batches; never infer SKU/option/price/stock
        # equality from a listing summary. Full sync uses individual reads as an
        # independent fallback when diagnosing batch endpoint differences.
        inventory_ids = [lid for lid, listing in summaries.items() if listing.get("state") != "removed"]
        inventories = {}
        for offset in range(0, len(inventory_ids), 100):
            if self.stop.is_set():
                raise ReviewError("Sync paused because the application is closing.")
            ids = inventory_ids[offset:offset+100]
            if not full:
                report("Checking variation inventories", total=len(inventory_ids))
                result = api.request("/listings/batch/inventory?" + urllib.parse.urlencode({"listing_ids": ",".join(ids)}))
                stats["inventory_requests"] += 1
                rows = result.get("results")
                if not isinstance(rows, list):
                    raise ReviewError("Etsy returned incomplete inventory data. The previous sync is preserved; try again.")
                seen = set()
                for row in rows:
                    lid = str(row.get("listing_id"))
                    if lid not in ids or lid in seen or str(row.get("shop_id")) != str(shop_id):
                        raise ReviewError("Etsy returned unexpected inventory data. The previous sync is preserved; try again.")
                    seen.add(lid)
                    inventories[lid] = row.get("inventory")
                if seen != set(ids):
                    raise ReviewError("Etsy returned incomplete inventory data. The previous sync is preserved; try again.")
            for lid in ids:
                # Null inventory is documented for listings never edited using
                # Etsy inventory tools. Use the established single-listing read.
                if full or inventories.get(lid) is None:
                    report("Checking variation inventories", summaries[lid].get("title", ""), len(inventory_ids))
                    inventories[lid] = api.request("/listings/" + lid + "/inventory?max_variations_supported=3")
                    stats["inventory_requests"] += 1
                inventory = inventories[lid]
                if not isinstance(inventory, dict) or not isinstance(inventory.get("products"), list):
                    raise ReviewError("Etsy returned incomplete inventory data. The previous sync is preserved; try again.")
                stats["inventories_checked"] += 1
            report("Checking variation inventories", total=len(inventory_ids))
        updates = []
        stats["checked"] = len(summaries)
        for lid, listing in summaries.items():
            if self.stop.is_set():
                raise ReviewError("Sync paused because the application is closing.")
            old = cached.get(lid)
            if lid in inventories:
                listing["snapshot"] = snapshot(listing, inventories[lid])
            elif old:
                listing["snapshot"] = {"fields": listing_fields(listing), "inventory": old["snapshot"]["inventory"]}
            else:
                raise ReviewError("Etsy returned a removed product without inventory history. Try syncing again.")
            if full or not old or listing["snapshot"]["inventory"] != old.get("snapshot", {}).get("inventory"):
                stats["refreshed"] += 1
            else:
                stats["reused"] += 1
            if old and old.get("state") != listing.get("state"):
                stats["status_changes"] += 1
            listing["category_name"] = taxonomy.get("names", {}).get(str(listing.get("taxonomy_id")), "Other products")
            if full or listing != old:
                updates.append(listing)
        stats.update(removed=len(removed), elapsed=time.time()-started, finished_at=time.time())
        shop.update(last_sync=stats["finished_at"], last_sync_started=started, sections=sections, sync_stats=stats)
        if full:
            shop["last_full_sync"] = shop["last_sync"]
        report("Saving sync results", total=len(inventory_ids))
        self.store.commit_shop_sync(shop_id, updates, removed, shop)

    def start_sync(self, shop_id=None, full=False):
        if not isinstance(full, bool):
            raise ReviewError("Choose a regular sync or a full sync.")
        def task():
            for shop in ([self.shop(shop_id)] if shop_id else self.store.all("shop")):
                current = self.shop(str(shop["shop_id"]))
                current["next_sync"] = time.time() + int(self.store.get("setting", "sync_minutes", 15)) * 60
                self.store.put("shop", str(shop["shop_id"]), current)
                self.sync_shop(str(shop["shop_id"]), full=full)
        self.background("Full shop sync…" if full else "Checking for changed products…", task)

    def scheduler(self):
        # One worker owns network mutations and sync; no overlapping background jobs.
        while not self.stop.wait(5):
            interval = int(self.store.get("setting", "sync_minutes", 15)) * 60
            due = [s for s in self.store.all("shop") if not s.get("sample") and time.time() - (s.get("last_sync") or 0) >= interval and time.time() >= s.get("next_sync", 0)]
            if due and not self.status["busy"]:
                shop_id = str(due[0]["shop_id"])
                shop = self.shop(shop_id)
                # A failed sync backs off instead of repeating a full crawl every 5 s.
                shop["next_sync"] = time.time() + interval
                self.store.put("shop", shop_id, shop)
                try:
                    self.start_sync(shop_id)
                except ReviewError:
                    pass

    def build_review(self, data):
        shop_id = str(data.get("shop_id", ""))
        shop = self.shop(shop_id)
        ids = list(dict.fromkeys(str(int(i)) for i in data.get("listing_ids", [])))
        if not ids:
            raise ReviewError("Choose at least one product.")
        filters = data.get("filters") or {}
        rules = data.get("rules") or []
        if not isinstance(filters, dict) or not isinstance(rules, list):
            raise ReviewError("These changes could not be read. Please prepare them again.")
        batch = {"id": str(uuid.uuid4()), "shop_id": shop_id, "shop_name": shop["name"], "created_at": time.time(), "status": "review", "label": str(data.get("label") or "Bulk update")[:100], "sample": bool(shop.get("sample")), "rules": rules, "filters": filters, "items": []}
        for lid in ids:
            listing = self.store.get("listing", lid)
            if not listing or str(listing["shop_id"]) != shop_id:
                raise ReviewError("A selected product does not belong to this shop. Refresh your selection.")
            before = listing["snapshot"]
            item = {"listing_id": lid, "title": listing["title"], "before": before, "fields_done": False, "inventory_done": False}
            try:
                applicable = [r for r in rules if not r.get("listing_ids") or lid in [str(x) for x in r["listing_ids"]]]
                if not applicable:
                    item.update(after=before, changes=[], status="unchanged", error="")
                    batch["items"].append(item)
                    continue
                resolved = self.resolve_rules(listing, applicable, shop)
                after, changes = preview(before, resolved, filters)
                item.update(after=after, changes=changes, status="ready" if changes else "unchanged", error="")
            except (ReviewError, ValueError, TypeError, KeyError) as exc:
                item.update(after=before, changes=[], status="attention", error=str(exc))
            batch["items"].append(item)
        self.store.put("batch", batch["id"], batch)
        self.touch()
        return batch

    def resolve_rules(self, listing, rules, shop):
        result = [{k: copy.deepcopy(v) for k, v in r.items() if not k.startswith("_")} for r in rules]
        for rule in result:
            if rule.get("kind") != "option" or rule.get("mode") == "remove":
                continue
            if rule.get("mode") == "copy" and rule.get("auto_source"):
                from engine import matches
                candidates = [p for product in listing["snapshot"]["inventory"]["products"] if matches(product, rule.get("filters") or {}) for p in product["property_values"] if str(p["property_id"]) == str(rule.get("property_id"))]
                if candidates and not any(rule.get("old") in p.get("values", []) for p in candidates):
                    rule["old"] = candidates[0]["values"][0]
            props = [p for product in listing["snapshot"]["inventory"]["products"] for p in product["property_values"] if str(p["property_id"]) == str(rule.get("property_id")) and rule.get("old") in p.get("values", [])]
            scaled = next((p for p in props if p.get("scale_id")), None)
            if scaled:
                # Existing values are already verified against the listing's scale.
                known = next((p for product in listing["snapshot"]["inventory"]["products"] for p in product["property_values"] if p["property_id"] == scaled["property_id"] and p.get("scale_id") == scaled["scale_id"] and p.get("values") == [rule.get("value")]), None)
                if known:
                    rule["_resolved"] = {"values": known["values"], "value_ids": known["value_ids"], "scale_id": known["scale_id"]}
                else:
                    raise ReviewError("This option uses an Etsy size scale. This version can copy or rename to a value already present on that scale; adding a new scaled size needs taxonomy selection.")
        return result

    def batch(self, batch_id):
        batch = self.store.get("batch", batch_id)
        if not batch:
            raise ReviewError("This review could not be found.")
        return batch

    def apply(self, batch_id, approval, dependency_approvals=None):
        batch = self.batch(batch_id)
        if approval != fingerprint(batch):
            raise ReviewError("The review changed. Open it again and approve the latest version.")
        if batch["status"] == "discarded":
            raise ReviewError("This review was discarded. Prepare a new review.")
        self.check_dependency_approval(batch, dependency_approvals)
        self.background("Applying approved changes…", lambda: self.apply_batch(batch_id, dependency_approvals))

    def check_dependency_approval(self, batch, approved):
        if batch.get("type") == "images":
            return
        if not isinstance(approved, (list, tuple, type(None))):
            raise ReviewError("Review the required stock and SKU setting changes separately.")
        allowed = set(str(lid) for lid in (approved or []))
        for item in batch["items"]:
            if item["status"] in ("ready", "failed", "uncertain", "interrupted") and changed_dependencies(item["before"], item["after"]) and item["listing_id"] not in allowed:
                raise ReviewError("Choose whether to allow the required variation setting changes for each product, or return to editing to keep its current settings.")

    def apply_batch(self, batch_id, dependency_approvals=None):
        batch = self.batch(batch_id)
        if batch.get("type") == "images":
            return self.images.apply(batch)
        self.check_dependency_approval(batch, dependency_approvals)
        shop = self.shop(batch["shop_id"])
        batch["status"] = "applying"
        started = time.perf_counter()
        timing = batch["timing"] = {"started_at": time.time(), "elapsed": 0, "etsy_read": 0, "etsy_write": 0, "rate_wait": 0, "local_save": 0}
        api = None if shop.get("sample") else Etsy(self, batch["shop_id"])
        total = sum(i["status"] not in ("unchanged", "attention", "conflict") for i in batch["items"])
        active = None
        processed = 0
        for entry in batch["items"]:
            entry["timings"] = []

        def progress(step):
            timing["elapsed"] = time.perf_counter() - started
            with self.status_lock:
                self.status["progress"] = {"batch_id": batch_id, "completed": sum(i["status"] == "complete" for i in batch["items"]), "processed": processed, "total": total, "product": active["title"] if active else "", "step": step, "step_started_at": time.time(), "started_at": timing["started_at"]}
                self.status["revision"] += 1

        def persist():
            progress("Save progress on this computer")
            tick = time.perf_counter()
            self.store.put("batch", batch_id, batch)
            timing["local_save"] += time.perf_counter() - tick

        def measure(label, category, fn):
            progress(label)
            tick = time.perf_counter()
            error = False
            if api:
                api.last_request_timing = {"http": 0, "wait": 0}
            try:
                return fn()
            except Exception:
                error = True
                raise
            finally:
                duration = time.perf_counter() - tick
                details = getattr(api, "last_request_timing", {}) if api else {}
                timing[category] += details.get("http", duration)
                timing["rate_wait"] += details.get("wait", 0)
                if active is not None:
                    active.setdefault("timings", []).append({"step": label, "seconds": duration, "http_seconds": details.get("http", duration), "wait_seconds": details.get("wait", 0), "failed": error})

        def local_save(kind, key, value):
            tick = time.perf_counter()
            self.store.put(kind, key, value)
            timing["local_save"] += time.perf_counter() - tick

        digest = lambda value: fingerprint(value, include_order=True)
        persist()
        for item in batch["items"]:
            if item["status"] in ("complete", "unchanged", "attention", "conflict"):
                if item["status"] == "complete":
                    processed += 1
                continue
            active = item
            item["timings"] = []
            try:
                if api:
                    listing, current = api.current(item["listing_id"], lambda label, fn: measure(label, "etsy_read", fn))
                else:
                    listing = self.store.get("listing", item["listing_id"])
                    current = listing["snapshot"]
                progress("Compare inventory on this computer")
                if digest(current) == digest(item["after"]):
                    item.update(status="complete", error="", fields_done=True, inventory_done=True, verified=current)
                    listing.update(current["fields"])
                    listing["snapshot"] = current
                    local_save("listing", item["listing_id"], listing)
                else:
                    intermediate = partial_snapshot(item["before"], item["after"], item.get("fields_done"), item.get("inventory_done"))
                    # Also allow a step whose HTTP response was lost: all desired
                    # values are absolute, so a retry never reapplies a percentage.
                    possible = [intermediate]
                    if item.get("phase") == "fields_sending":
                        possible.append(partial_snapshot(item["before"], item["after"], True, False))
                    if item.get("phase") == "inventory_sending":
                        possible.append(partial_snapshot(item["before"], item["after"], item.get("fields_done"), True))
                    if digest(current) not in {digest(p) for p in possible}:
                        item.update(status="conflict", error="This product changed since the preview. Sync and prepare a new review.")
                        persist()
                        continue
                    field_data = {k: v for k, v in item["after"]["fields"].items() if current["fields"].get(k) != v}
                    if field_data:
                        item["phase"] = "fields_sending"
                        persist()
                        if api:
                            measure("Save product details to Etsy", "etsy_write", lambda: api.request("/shops/" + batch["shop_id"] + "/listings/" + item["listing_id"], "PATCH", field_data, form=True))
                        item["fields_done"] = True
                        item["phase"] = "fields_done"
                        persist()
                    if digest({"fields": item["after"]["fields"], "inventory": current["inventory"]}) != digest(item["after"]):
                        item["phase"] = "inventory_sending"
                        persist()
                        if api:
                            measure("Save variations to Etsy", "etsy_write", lambda: api.request("/listings/" + item["listing_id"] + "/inventory?max_variations_supported=3", "PUT", item["after"]["inventory"]))
                        item["inventory_done"] = True
                        item["phase"] = "inventory_done"
                        persist()
                    if api:
                        listing, verified = api.current(item["listing_id"], lambda label, fn: measure(label.replace("Read", "Verify", 1), "etsy_read", fn))
                    else:
                        verified = copy.deepcopy(item["after"])
                    progress("Check saved variations on this computer")
                    if digest(verified) != digest(item["after"]):
                        message = "Etsy returned a different option order. Sync and check the saved listing before preparing another review." if fingerprint(verified) == fingerprint(item["after"]) else "Etsy's returned values differ from the approved review. Sync and check this product before retrying."
                        item.update(status="uncertain", error=message, verified=verified)
                    else:
                        item.update(status="complete", error="", verified=verified)
                        listing.update(verified["fields"])
                        listing["snapshot"] = verified
                        local_save("listing", item["listing_id"], listing)
                persist()
            except (ReviewError, ValueError) as exc:
                item.update(status="uncertain" if item.get("phase", "").endswith("sending") else "failed", error=str(exc))
                persist()
            except Exception:
                item.update(status="uncertain", error="This update could not be verified. Review it before trying again.")
                persist()
            finally:
                processed += 1
                progress("Product check finished")
        batch["status"] = "complete" if all(i["status"] in ("complete", "unchanged") for i in batch["items"]) else "needs_attention"
        persist()
        timing["elapsed"] = time.perf_counter() - started
        self.store.put("batch", batch_id, batch)

    def restore_review(self, batch_id):
        original = self.batch(batch_id)
        if original.get("type") == "images":
            raise ReviewError("Image changes cannot be undone with the inventory restore button. Keep original files; use the Images tab to add them again if needed.")
        batch = {**copy.deepcopy(original), "id": str(uuid.uuid4()), "created_at": time.time(), "status": "review", "label": "Restore · " + original["label"], "restore_of": original["id"], "items": []}
        for item in original["items"]:
            if item["status"] != "complete":
                continue
            changes = [{**copy.deepcopy(c), "before": c["after"], "after": c["before"], "kind": "removed" if c.get("kind") == "added" else "added" if c.get("kind") == "removed" else c.get("kind", "updated")} for c in item["changes"]]
            batch["items"].append({"listing_id": item["listing_id"], "title": item["title"], "before": item.get("verified", item["after"]), "after": item["before"], "changes": changes, "status": "ready", "error": "", "fields_done": False, "inventory_done": False})
        batch.pop("timing", None)
        if not batch["items"]:
            raise ReviewError("There are no completed changes to restore.")
        self.store.put("batch", batch["id"], batch)
        self.touch()
        return batch

    def preset(self, data):
        shop = self.shop(str(data.get("shop_id")))
        name = str(data.get("name", "")).strip()
        if not name or not isinstance(data.get("rules"), list):
            raise ReviewError("Give this saved edit a name and add changes first.")
        preset_id = str(data.get("id") or uuid.uuid4())
        existing = self.store.get("preset", preset_id)
        if data.get("id") and (not existing or existing["shop_id"] != str(shop["shop_id"])):
            raise ReviewError("This saved shortcut does not belong to the selected shop.")
        value = {"id": preset_id, "shop_id": str(shop["shop_id"]), "name": name[:80], "rules": data["rules"], "filters": data.get("filters") or {}}
        self.store.put("preset", value["id"], value)
        self.touch()
        return value

    def sample(self):
        if self.store.get("shop", "sample"):
            return
        shop = {"shop_id": "sample", "name": "Sample Tee Shop", "currency": "USD", "sample": True, "last_sync": time.time(), "sections": [{"shop_section_id": 1, "title": "Everyday favorites"}, {"shop_section_id": 2, "title": "Personalized gifts"}]}
        names = ["Weekend Club · Everyday Tee", "Made for Slow Mornings", "The Original Dad Club", "Good Things Take Time", "Wildflower State of Mind", "Your Favorite Person", "Sunshine Social Club", "A Little More Kindness"]
        for i, title in enumerate(names):
            listing = {"listing_id": 90000 + i, "shop_id": "sample", "title": title, "description": "An original, made-to-order design. Soft everyday clothing in your favorite color.", "tags": ["gift", "graphic tee", "everyday style"], "materials": ["cotton"], "shop_section_id": 1 if i % 2 == 0 else 2, "taxonomy_id": 482 if i % 3 else 526, "category_name": "T-shirts" if i % 3 else "Sweatshirts", "state": "draft" if i == 7 else "active", "art": i % 4}
            products = []
            for style in ("T-shirt / S", "T-shirt / M", "T-shirt / L", "Sweatshirt / M", "Sweatshirt / L"):
                for color in ("Ivory", "Pepper", "Sage"):
                    products.append({"sku": "DEMO-" + str(i) + "-" + str(len(products)), "property_values": [{"property_id": 513, "property_name": "Style / Size", "value_ids": [], "values": [style]}, {"property_id": 514, "property_name": "Color", "value_ids": [], "values": [color]}], "offerings": [{"price": 34 if style.startswith("Sweatshirt") else 22, "quantity": 20, "is_enabled": True, "readiness_state_id": None}]})
            listing["snapshot"] = snapshot(listing, {"products": products, "price_on_property": [513], "quantity_on_property": [513, 514], "sku_on_property": [513, 514]})
            self.store.put("listing", str(listing["listing_id"]), listing)
        self.store.put("shop", "sample", shop)
        self.touch()
