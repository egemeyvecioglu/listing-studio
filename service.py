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

from engine import changed_dependencies, LISTING_FIELDS, ReviewError, fingerprint, partial_snapshot, preview, snapshot
from storage import Vault

API = "https://api.etsy.com/v3"


class EtsyError(ReviewError):
    pass


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
            raise EtsyError("Etsy did not allow this request. Reconnect the shop and check the app's approved access.")
        # Return API validation detail without echoing tokens/headers or request bodies.
        try:
            error = json.loads(exc.read(4096))
            message = str(error.get("error", ""))[:350]
        except (ValueError, AttributeError):
            message = ""
        raise EtsyError("Etsy could not complete the request (" + str(exc.code) + "). " + message)
    except (urllib.error.URLError, TimeoutError, OSError):
        raise EtsyError("Etsy could not be reached. A submitted update may still have completed. Check its status before retrying.")


class Etsy:
    def __init__(self, service, shop_id):
        self.service, self.shop_id = service, str(shop_id)
        self.next_request = 0

    def request(self, path, method="GET", data=None, form=False, content_type=None):
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
            time.sleep(delay)
        self.next_request = time.monotonic() + 0.55
        request_headers = {"x-api-key": account["key"] + ":" + account["secret"], "Authorization": "Bearer " + account["access_token"]}
        if content_type:
            request_headers["Content-Type"] = content_type
        result, headers = request_json(API + "/application" + path, method, data, request_headers, form)
        # Respect the actual allocated quota, rather than docs' example numbers.
        limit = headers.get("x-limit-per-second") or headers.get("X-Limit-Per-Second")
        if limit:
            try:
                self.next_request = time.monotonic() + max(0.55, 1.05 / max(1, float(limit)))
            except ValueError:
                pass
        return result

    def current(self, listing_id):
        listing = self.request("/listings/" + str(listing_id))
        if str(listing.get("shop_id")) != self.shop_id:
            raise ReviewError("This product belongs to a different shop.")
        inventory = self.request("/listings/" + str(listing_id) + "/inventory?max_variations_supported=3")
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

    def background(self, message, fn):
        if not self.operation.acquire(False):
            raise ReviewError("Another task is running. Please wait for it to finish.")
        with self.status_lock:
            self.status.update(busy=True, message=message, error="")

        def run():
            try:
                fn()
            except Exception as exc:
                with self.status_lock:
                    self.status["error"] = str(exc) if isinstance(exc, (ReviewError, ValueError)) else "The task stopped unexpectedly. Your saved review is preserved."
            finally:
                with self.status_lock:
                    self.status.update(busy=False, message="")
                self.touch()
                self.operation.release()
        threading.Thread(target=run, daemon=True).start()

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

    def sync_shop(self, shop_id):
        shop = self.shop(shop_id)
        if shop.get("sample"):
            shop["last_sync"] = time.time()
            self.store.put("shop", shop_id, shop)
            return
        api, listings, seen = Etsy(self, shop_id), [], set()
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
        for state in ("active", "draft", "inactive", "expired", "sold_out"):
            offset = 0
            while True:
                if self.stop.is_set():
                    raise ReviewError("Sync paused because the application is closing.")
                page = api.request("/shops/" + str(shop_id) + "/listings?" + urllib.parse.urlencode({"state": state, "limit": 100, "offset": offset, "includes": "Images"}))
                rows = page.get("results", [])
                for listing in rows:
                    lid = str(listing["listing_id"])
                    if str(listing.get("shop_id")) != str(shop_id):
                        raise ReviewError("Sync returned a product from a different shop.")
                    if lid in seen:
                        continue
                    seen.add(lid)
                    inventory = api.request("/listings/" + lid + "/inventory?max_variations_supported=3")
                    listing["snapshot"] = snapshot(listing, inventory)
                    listing["category_name"] = taxonomy.get("names", {}).get(str(listing.get("taxonomy_id")), "Other products")
                    listings.append(listing)
                    with self.status_lock:
                        self.status["message"] = "Syncing " + shop["name"] + " · " + str(len(listings)) + " products"
                offset += len(rows)
                if not rows or offset >= page.get("count", offset):
                    break
        shop.update(last_sync=time.time(), sections=sections)
        self.store.replace_shop_listings(shop_id, listings, shop)

    def start_sync(self, shop_id=None):
        def task():
            for shop in ([self.shop(shop_id)] if shop_id else self.store.all("shop")):
                self.sync_shop(str(shop["shop_id"]))
        self.background("Syncing products…", task)

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
        self.store.put("batch", batch_id, batch)
        api = None if shop.get("sample") else Etsy(self, batch["shop_id"])
        for item in batch["items"]:
            if item["status"] in ("complete", "unchanged", "attention", "conflict"):
                continue
            try:
                if api:
                    listing, current = api.current(item["listing_id"])
                else:
                    listing = self.store.get("listing", item["listing_id"])
                    current = listing["snapshot"]
                if fingerprint(current) == fingerprint(item["after"]):
                    item.update(status="complete", error="", fields_done=True, inventory_done=True, verified=current)
                    listing.update(current["fields"])
                    listing["snapshot"] = current
                    self.store.put("listing", item["listing_id"], listing)
                else:
                    intermediate = partial_snapshot(item["before"], item["after"], item.get("fields_done"), item.get("inventory_done"))
                    # Also allow a step whose HTTP response was lost: all desired
                    # values are absolute, so a retry never reapplies a percentage.
                    possible = [intermediate]
                    if item.get("phase") == "fields_sending":
                        possible.append(partial_snapshot(item["before"], item["after"], True, False))
                    if item.get("phase") == "inventory_sending":
                        possible.append(partial_snapshot(item["before"], item["after"], item.get("fields_done"), True))
                    if fingerprint(current) not in {fingerprint(p) for p in possible}:
                        item.update(status="conflict", error="This product changed since the preview. Sync and prepare a new review.")
                        self.store.put("batch", batch_id, batch)
                        continue
                    field_data = {k: v for k, v in item["after"]["fields"].items() if current["fields"].get(k) != v}
                    if field_data:
                        item["phase"] = "fields_sending"
                        self.store.put("batch", batch_id, batch)
                        if api:
                            api.request("/shops/" + batch["shop_id"] + "/listings/" + item["listing_id"], "PATCH", field_data, form=True)
                        item["fields_done"] = True
                        item["phase"] = "fields_done"
                        self.store.put("batch", batch_id, batch)
                    if fingerprint({"fields": item["after"]["fields"], "inventory": current["inventory"]}) != fingerprint(item["after"]):
                        item["phase"] = "inventory_sending"
                        self.store.put("batch", batch_id, batch)
                        if api:
                            api.request("/listings/" + item["listing_id"] + "/inventory?max_variations_supported=3", "PUT", item["after"]["inventory"])
                        item["inventory_done"] = True
                        item["phase"] = "inventory_done"
                        self.store.put("batch", batch_id, batch)
                    if api:
                        listing, verified = api.current(item["listing_id"])
                    else:
                        verified = copy.deepcopy(item["after"])
                    if fingerprint(verified) != fingerprint(item["after"]):
                        item.update(status="uncertain", error="Etsy's returned values differ from the approved review. Sync and check this product before retrying.")
                    else:
                        item.update(status="complete", error="", verified=verified)
                        listing.update(verified["fields"])
                        listing["snapshot"] = verified
                        self.store.put("listing", item["listing_id"], listing)
                self.store.put("batch", batch_id, batch)
            except (ReviewError, ValueError) as exc:
                item.update(status="uncertain" if item.get("phase", "").endswith("sending") else "failed", error=str(exc))
                self.store.put("batch", batch_id, batch)
            except Exception:
                item.update(status="uncertain", error="This update could not be verified. Review it before trying again.")
                self.store.put("batch", batch_id, batch)
        batch["status"] = "complete" if all(i["status"] in ("complete", "unchanged") for i in batch["items"]) else "needs_attention"
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
