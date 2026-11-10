"""Pure batch calculation. No network calls, credentials, or hidden writes."""
import copy
import hashlib
import json
from itertools import combinations
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


class ReviewError(ValueError):
    pass


LISTING_FIELDS = {"title", "description", "tags", "materials", "shop_section_id"}
DEPENDENCIES = ("price_on_property", "quantity_on_property", "sku_on_property", "readiness_state_on_property")


def decimal(value):
    try:
        number = Decimal(str(value))
        if not number.is_finite():
            raise ValueError()
        return number
    except (InvalidOperation, ValueError, TypeError):
        raise ReviewError("Enter a valid number.")


def money(value):
    if isinstance(value, dict):
        return decimal(value["amount"]) / decimal(value["divisor"])
    return decimal(value)


def snapshot(listing, inventory):
    """Only writable inventory fields survive. Preserve every property dependency."""
    products = []
    for product in inventory.get("products", []):
        if product.get("is_deleted"):
            continue
        props = []
        for p in product.get("property_values", []):
            item = {k: copy.deepcopy(p[k]) for k in ("property_id", "property_name", "scale_id", "value_ids", "values") if k in p and p[k] is not None}
            item["scale_id"] = p.get("scale_id") or None
            item.setdefault("value_ids", [])
            props.append(item)
        offerings = []
        for o in product.get("offerings", []):
            if o.get("is_deleted"):
                continue
            item = {"price": float(money(o["price"])), "quantity": int(o["quantity"]), "is_enabled": bool(o.get("is_enabled", True)), "readiness_state_id": o.get("readiness_state_id") or None}
            offerings.append(item)
        products.append({"sku": product.get("sku") or "", "property_values": sorted(props, key=lambda p: p["property_id"]), "offerings": offerings})
    fields = {k: copy.deepcopy(listing.get(k, [] if k in ("tags", "materials") else "")) for k in sorted(LISTING_FIELDS)}
    for k in ("tags", "materials"):
        fields[k] = fields[k] or []
    fields["shop_section_id"] = listing.get("shop_section_id") or 0
    return {"fields": fields, "inventory": {"products": products, **{k: sorted(inventory.get(k) or []) for k in DEPENDENCIES}}}


def product_key(product):
    return json.dumps([(p["property_id"], p.get("scale_id"), p.get("values", [])) for p in product.get("property_values", [])], ensure_ascii=False, sort_keys=True)


def variation_order(inventory):
    result = {}
    for product in inventory.get("products", []):
        for prop in product.get("property_values", []):
            values = result.setdefault(str(prop["property_id"]), [])
            for value in prop.get("values", []):
                if value not in values:
                    values.append(value)
    return result


def fingerprint(value, include_order=False):
    value = copy.deepcopy(value)
    if isinstance(value, dict) and "inventory" in value:
        if include_order:
            value["variation_order"] = variation_order(value["inventory"])
        # Etsy assigns IDs to new values after PUT. Compare their meaning, not IDs.
        for p in value["inventory"]["products"]:
            for prop in p["property_values"]:
                prop.pop("value_ids", None)
                prop.pop("property_name", None)
        value["inventory"]["products"].sort(key=product_key)
        for field in ("tags", "materials"):
            value["fields"][field] = sorted(value["fields"].get(field) or [])
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def choices(product):
    return {str(p["property_id"]): p.get("values", []) for p in product.get("property_values", [])}


def matches(product, filters):
    values = choices(product)
    return all(not allowed or any(v in allowed for v in values.get(str(prop), [])) for prop, allowed in filters.items())


def label(product):
    return " / ".join(", ".join(p.get("values", [])) for p in product.get("property_values", [])) or "Standard option"


def text_edit(old, rule):
    mode, value = rule.get("mode", "set"), str(rule.get("value", ""))
    if mode == "set":
        return value
    if mode == "prefix":
        return value + old
    if mode == "suffix":
        return old + value
    if mode == "replace" and rule.get("find"):
        return old.replace(str(rule["find"]), value)
    raise ReviewError("Choose a supported text change and enter text to find when replacing.")


def preview(before, rules, filters=None):
    after = copy.deepcopy(before)
    changes, touched, matched = [], set(), 0
    filters = filters or {}
    default_filters = filters
    if not rules:
        raise ReviewError("Add at least one change.")

    def record(path, heading, old, new):
        if path in touched:
            raise ReviewError("Two changes affect the same value: " + heading + ". Remove one change or combine them.")
        touched.add(path)
        if old != new:
            changes.append({"label": heading, "before": old, "after": new})

    # Resolve independent edits against the original snapshot, never incrementally.
    for ri, rule in enumerate(rules):
        filters = rule.get("filters", default_filters)
        kind = rule.get("kind")
        if kind == "listing":
            field = rule.get("field")
            if field not in LISTING_FIELDS:
                raise ReviewError("This listing field is not supported.")
            old = before["fields"][field]
            if field in ("tags", "materials"):
                value = rule.get("value", [])
                value = [v.strip() for v in (value.split(",") if isinstance(value, str) else value) if isinstance(v, str) and v.strip()]
                if rule.get("mode", "set") == "add":
                    new = list(dict.fromkeys(old + value))
                elif rule.get("mode") == "remove":
                    new = [v for v in old if v not in value]
                elif rule.get("mode", "set") == "set":
                    new = list(dict.fromkeys(value))
                else:
                    raise ReviewError("Choose replace, add, or remove for tags and materials.")
                if len(new) > 13 or any(len(v) > (20 if field == "tags" else 45) for v in new):
                    raise ReviewError("Too many or overly long " + field + ".")
            elif field == "shop_section_id":
                new = int(rule.get("value") or 0)
                if new < 0:
                    raise ReviewError("Choose a valid shop section.")
            else:
                new = text_edit(str(old), rule)
                if not new.strip() or (field == "title" and len(new) > 140):
                    raise ReviewError("Titles must contain 1–140 characters; descriptions cannot be empty.")
            record("fields." + field, field.replace("_", " ").title(), old, new)
            after["fields"][field] = new
            continue
        if kind not in ("price", "quantity", "sku", "enabled", "option"):
            raise ReviewError("Choose a supported change.")
        found = 0
        sources = None
        if kind == "option" and rule.get("mode") == "copy" and rule.get("auto_source"):
            # Use each listing's own combinations; a global source value may
            # be absent for some colors/sizes, and existing targets stay intact.
            groups = {}
            prop_id = int(rule.get("property_id", 0))
            for pi, product in enumerate(before["inventory"]["products"]):
                prop = next((p for p in product["property_values"] if p["property_id"] == prop_id), None)
                if prop is not None and matches(product, filters):
                    key = json.dumps([(p["property_id"], p.get("scale_id"), p.get("values", [])) for p in product["property_values"] if p["property_id"] != prop_id], sort_keys=True)
                    groups.setdefault(key, []).append((pi, prop))
            sources = set()
            for candidates in groups.values():
                if any(rule.get("value") in prop.get("values", []) for _, prop in candidates):
                    found += 1
                    continue
                source = next((pi for pi, prop in candidates if rule.get("old") in prop.get("values", [])), candidates[0][0])
                sources.add(source)
        for pi, product in enumerate(before["inventory"]["products"]):
            if not matches(product, filters):
                continue
            if kind == "option":
                prop_id = int(rule.get("property_id", 0))
                prop = next((p for p in product["property_values"] if p["property_id"] == prop_id), None)
                if prop is None or (pi not in sources if sources is not None else rule.get("old") not in prop.get("values", [])):
                    continue
                found += 1
                new_summary = "Remove combination" if rule.get("mode") == "remove" else {"Option": rule.get("value"), "Price": rule.get("price") if rule.get("price") not in (None, "") else product["offerings"][0]["price"], "Stock": rule.get("quantity") if rule.get("quantity") not in (None, "") else product["offerings"][0]["quantity"]}
                structure_path = "product." + str(pi) + ".structure"
                if rule.get("mode") == "copy":
                    structure_path += ".copy." + str(rule.get("value"))
                record(structure_path, label(product), None if rule.get("mode") == "copy" else prop.get("values"), new_summary)
                if rule.get("mode") == "copy":
                    changes[-1].update(kind="added", option=str(rule.get("value")), colors=[v for p in product["property_values"] if "color" in p.get("property_name", "").lower() and p["property_id"] != prop_id for v in p.get("values", [])], label=" / ".join(str(rule.get("value")) if p["property_id"] == prop_id else ", ".join(p.get("values", [])) for p in product["property_values"]))
                target = after["inventory"]["products"][pi]
                if rule.get("mode") == "remove":
                    target["_remove"] = True
                elif rule.get("mode") in ("rename", "copy"):
                    name = str(rule.get("value", "")).strip()
                    if not name:
                        raise ReviewError("Enter a name for the new option.")
                    if rule.get("mode") == "copy":
                        # Copy from the original; reject overlaps with edits to this source.
                        target = copy.deepcopy(product)
                        target["_new"] = True
                        target["sku"] = str(rule["sku"]) if rule.get("sku") else product["sku"]
                        for o in target["offerings"]:
                            if rule.get("price") not in (None, ""):
                                o["price"] = float(decimal(rule["price"]).quantize(Decimal(".01"), rounding=ROUND_HALF_UP))
                            if rule.get("quantity") not in (None, ""):
                                q = decimal(rule["quantity"])
                                if q != q.to_integral_value():
                                    raise ReviewError("Quantity must be a whole number.")
                                o["quantity"] = int(q)
                        after["inventory"]["products"].append(target)
                    target_prop = next(p for p in target["property_values"] if p["property_id"] == prop_id)
                    target_prop["values"] = [name]
                    target_prop["value_ids"] = []
                    if prop.get("scale_id"):
                        resolved = rule.get("_resolved")
                        if not resolved:
                            raise ReviewError("This option uses Etsy's size scale. Choose a valid value from that scale.")
                        target_prop.update(resolved)
                else:
                    raise ReviewError("Choose rename, copy, or remove for a variation option.")
                continue
            found += 1
            if kind == "sku":
                new = text_edit(product["sku"], rule)
                if len(new) > 32:
                    raise ReviewError("SKUs must be 32 characters or fewer.")
                record("product." + str(pi) + ".sku", label(product) + " · SKU", product["sku"], new)
                after["inventory"]["products"][pi]["sku"] = new
                continue
            for oi, offering in enumerate(product["offerings"]):
                field = {"price": "price", "quantity": "quantity", "enabled": "is_enabled"}[kind]
                old = offering[field]
                if kind == "enabled":
                    if not isinstance(rule.get("value"), bool):
                        raise ReviewError("Choose available or unavailable.")
                    new = rule["value"]
                else:
                    amount, base = decimal(rule.get("value")), decimal(old)
                    mode = rule.get("mode", "set")
                    if mode == "set":
                        new = amount
                    elif mode == "add":
                        new = base + amount
                    elif mode == "subtract":
                        new = base - amount
                    elif mode == "percent" and kind == "price":
                        new = base * (1 + amount / 100)
                    else:
                        raise ReviewError("Choose a supported number change.")
                    if kind == "price":
                        rounding = decimal(rule.get("rounding") or ".01")
                        if rounding <= 0:
                            raise ReviewError("Rounding must be positive.")
                        new = (new / rounding).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * rounding
                        new = float(new.quantize(Decimal(".01"), rounding=ROUND_HALF_UP))
                    else:
                        if new != new.to_integral_value():
                            raise ReviewError("Quantity must be a whole number.")
                        new = int(new)
                record("product." + str(pi) + "." + str(oi) + "." + field, label(product) + " · " + kind.title(), old, new)
                after["inventory"]["products"][pi]["offerings"][oi][field] = new
        matched += found
        if not found:
            raise ReviewError("No variation matches this change. Check the option name and variation filters.")

    products = after["inventory"]["products"]
    for pi in range(len(before["inventory"]["products"])):
        structure = "product." + str(pi) + ".structure"
        has_structure = any(p == structure or p.startswith(structure + ".") for p in touched)
        non_structure = any(p.startswith("product." + str(pi) + ".") and not p.startswith(structure) for p in touched)
        mixed_structure = structure in touched and any(p.startswith(structure + ".") for p in touched)
        if has_structure and (non_structure or mixed_structure):
            raise ReviewError("A variation structure change overlaps another edit. Apply and sync the structure change first.")
    after["inventory"]["products"] = [p for p in products if not p.pop("_remove", False)]
    for p in after["inventory"]["products"]:
        p.pop("_new", None)
    repair_dependencies(after["inventory"], changes)
    validate_inventory(after["inventory"])
    return after, changes


def repair_dependencies(inventory, changes):
    """Expand dependencies only when an approved edit creates a new difference."""
    products = inventory["products"]
    dimensions = sorted({p["property_id"] for product in products for p in product["property_values"]})
    for field, dependency in (("price", "price_on_property"), ("quantity", "quantity_on_property"), ("sku", "sku_on_property"), ("readiness_state_id", "readiness_state_on_property")):
        current = inventory.get(dependency) or []
        def consistent(dependencies):
            groups = {}
            for product in products:
                key = json.dumps([(p["property_id"], p["values"]) for p in product["property_values"] if p["property_id"] in dependencies], sort_keys=True)
                value = product["sku"] if field == "sku" else [o.get(field) for o in product["offerings"]]
                if key in groups and groups[key] != value:
                    return False
                groups[key] = value
            return True
        needs_three = len(dimensions) == 3 and len(current) == 2
        if not consistent(current) or needs_three:
            # Preserve current dependencies and add only the dimensions needed
            # to represent the requested values without modifying existing rows.
            remaining = [d for d in dimensions if d not in current]
            target = dimensions
            if not needs_three:
                for count in range(1, len(remaining) + 1):
                    candidate = next((sorted(current + list(extra)) for extra in combinations(remaining, count) if consistent(current + list(extra))), None)
                    if candidate is not None:
                        target = candidate
                        break
            inventory[dependency] = target
            if changes is not None and current != target:
                changes.append({"kind": "dependency", "dependency": dependency, "label": field.replace("_", " ").title() + " varies by", "before": current, "after": target, "reason": "The requested values differ between combinations that currently share this setting." if not needs_three else "This three-variation inventory requires the setting to include all three variation types."})


def changed_dependencies(before, after):
    return [key for key in DEPENDENCIES if (before.get("inventory", {}).get(key) or []) != (after.get("inventory", {}).get(key) or [])]



def validate_inventory(inventory):
    products = inventory.get("products", [])
    if not products:
        raise ReviewError("A listing must keep at least one variation combination.")
    seen, dimensions = set(), set()
    signature = None
    for product in products:
        key = product_key(product)
        if key in seen:
            raise ReviewError("This would create duplicate variation combinations.")
        seen.add(key)
        prop_set = tuple(sorted(p["property_id"] for p in product["property_values"]))
        if signature is None:
            signature = prop_set
        if signature != prop_set:
            raise ReviewError("Every combination must have the same variation types.")
        dimensions.update(prop_set)
        if len(dimensions) > 3:
            raise ReviewError("Etsy supports at most three variation types.")
        if not product["offerings"]:
            raise ReviewError("Every combination needs a price and quantity.")
        for o in product["offerings"]:
            if decimal(o["price"]) <= 0 or int(o["quantity"]) < 0:
                raise ReviewError("Prices must be positive and quantities cannot be negative.")
    limit = 2500 if len(dimensions) == 3 else 4900
    if len(products) > limit:
        raise ReviewError("This exceeds Etsy's variation-combination limit.")
    if len(dimensions) == 3:
        if any(len(inventory.get(k, [])) == 2 for k in DEPENDENCIES):
            raise ReviewError("Three variation types must use either one or all three types for a dependent field.")
        if any(len(inventory.get(k, [])) == 3 for k in DEPENDENCIES) and len(products) > 400:
            raise ReviewError("Etsy limits this variation-dependent inventory to 400 combinations.")


def partial_snapshot(before, after, fields_done, inventory_done):
    result = copy.deepcopy(before)
    if fields_done:
        result["fields"] = copy.deepcopy(after["fields"])
    if inventory_done:
        result["inventory"] = copy.deepcopy(after["inventory"])
    return result
