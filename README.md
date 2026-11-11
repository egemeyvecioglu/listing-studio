# Listing Studio

A personal project by [Ege Meyvecioğlu](https://github.com/egemeyvecioglu).
A local browser-based editor for Etsy products and variations. It runs on your
computer, with no hosted account system. The interface is English, with locale
files prepared for Turkish support.

The term 'Etsy' is a trademark of Etsy, Inc. This Application uses Etsy's API,
but is not endorsed or certified by Etsy.

## What it does

- Filter products by search, shop section, category, status, and variation values.
- Edit one product with its pencil, or select products to edit together.
  Single-product and bulk drafts stay separate. A review includes only the
  current selection; unrelated pending edits cannot expand its targets.
- Preserve existing option order and append new values in entry order.
- Drag existing or pending options by their handle, use up/down arrows, or choose a named
  Before/After position. Review the resulting order before saving.
- Add variation options with +; rename with the pencil; remove with the trash.
- Set prices from one shared row per style/size across selected listings;
  choose grouping dimensions and stage different prices for several groups.
- Leave new-option Price blank to inherit each matching combination’s price;
  set a price to override it. New-option Stock defaults to 999.
- Edit stock, SKUs, and availability in the individual inventory table.
- Update titles, descriptions, tags, materials, and shop sections.
- Add JPG/PNG images at the beginning or end of selected galleries.
- Remove individual images with the trash, or the first/last N images in bulk.
- Preview image order and the cover before approving any Etsy update.
- Review additions as Added / Not present, with a color-count summary per option.
- Review required variation-setting changes separately; each affected product
  needs separate consent before saving. Unchanged settings are preserved.
- Track completed products, the active save step, elapsed time, and separate
  Etsy read/write, request-allowance wait, and local-save timings.
- Review before/after changes before approving any Etsy update.
- Keep separate shop connections, saved shortcuts, pending edits, and history.
- Sync manually or periodically while the local service is running. First setup
  loads every inventory. Routine sync reads inventories in batches of up to 100
  and updates changed local records by comparing actual variation data, including
  prices, stock, SKUs, options and order. It does not rely on listing timestamps.
  All listing-status summary pages are still checked, including drafts, inactive,
  expired and sold-out products. **Full sync…** forces individual inventory reads
  and replaces the local inventory snapshots, useful for diagnosing batch results.
  Both methods download all inventory data; routine sync reduces request count,
  not payload size. Progress shows inventory checks; Settings shows changed and
  unchanged counts. Failed requests preserve the previous cache and checkpoint.
- Prepare local reviews during sync. Etsy saves wait until the named task
  finishes; pending saves are never automatically queued.
- Prepare restore previews and recover interrupted batches without automatically
  resuming Etsy writes.

## Windows setup from source

Requires Windows 10/11 and Python 3.10 or newer. Download or clone this repository,
then run **Setup Windows.cmd** once. It installs the Python dependency and asks
Windows to trust the local HTTPS certificate for the current user. The certificate
is generated on that PC; no shared certificate or private key is distributed.

For everyday use, double-click **Start Listing Studio.cmd**. Keep its window open.
Your browser opens at `https://localhost:8765`.

**Open Listing Studio.cmd** is for an installation that already has a bundled
`runtime/` folder. The runtime is machine-specific and is not tracked in Git.

## Etsy connection

1. Register an appropriate app in the [Etsy Developer Portal](https://www.etsy.com/developers/).
   Seller App access is restricted to its registered shop; broader use needs the
   access Etsy approves for that use case.
2. Register this exact callback URL in the Etsy app:
   `https://localhost:8765/oauth/callback`.
3. Choose **Connect with Etsy** in Listing Studio. Enter your own Keystring and
   Shared secret locally, then authorize the shop on Etsy.
4. Wait for the first sync. Inspect real products and variation values before
   approving an edit. Loading the full inventory of a large shop can take time.

The requested OAuth scopes are `shops_r`, `listings_r`, and `listings_w`.
Credentials stay in Windows DPAPI-protected storage or macOS Keychain. Windows
shop data and history are stored in `%LOCALAPPDATA%\EtsyListingStudio`, outside
the repository. macOS development data uses `.local/`, which is ignored by Git.
Do not include local data or secrets in issues, screenshots, or commits.

See [official Etsy references](REFERENCES.md) for API and policy sources.

## Updating with Codex

This repository is public: downloading updates does not require a GitHub account.
An initial Git clone needs Git installed on the computer. Codex can set up that
checkout and use it for subsequent updates.

Ask Codex:

> Update Listing Studio from https://github.com/egemeyvecioglu/listing-studio.
> Follow its AGENTS.md. Preserve my existing Etsy connection, local data, and
> runtime. If this is a ZIP installation, set up a Git checkout first. Start the
> updated app and verify sample behavior without changing my real Etsy products.

For an existing clean Git checkout, close the running app, use `git pull --ff-only`,
reopen the launcher, and refresh the browser. Do not overwrite local modifications
or reset application data to update the code. If dependencies changed, rerun setup.
There is no unattended updater in this version; updates happen when requested.

## Development and offline checks

```sh
python -m pip install -r requirements.txt
python app.py
```

Open `http://localhost:8765` and choose **Explore a sample shop**. Those products
are fictional; sample edits cannot update Etsy. Live connections require HTTPS:
`python app.py --https`. On macOS the generated local certificate needs one-time
trust; the Windows setup handles trust for the current user.

```sh
python check_edits.py
python check_images.py
python check_review.py
python check_tasks.py
python check_save_flow.py
python check_option_order.py
python check_sync.py
node --check web/app.js
node --check web/images.js
node check_prices.js
node check_editor_scope.js
node --check web/prices.js
node --check web/review.js
node --check web/option_order.js
```

## Current limits and validation

Batch inventory sync is covered by offline checks. Live batch/single-read parity
and whether individual variation edits change Etsy listing timestamps remain
unverified; the sync does not assume timestamp propagation.

The user has reported a successful Windows connection and listing sync. The new
direct-edit interface has been checked in a browser with sample products, and
focused offline edit and gallery checks pass. Gallery additions, removals, cover
order, and approval were checked with sample data. Real Etsy writes and the revised Windows
interface still need validation on the receiving PC.

- No image generation, image retouching, new listing creation, personalization
  editing, or fulfillment-provider synchronization yet.
- Galleries support up to 20 images. Images linked to variations cannot be
  removed here. Image removals have no in-app restore; keep the original files.
  An uncertain upload is blocked from automatic retry to avoid duplicates.
- Existing variation types can be managed. Adding an entirely new variation
  dimension or arbitrary new values on Etsy's size scales is not implemented.
- Clothing type and size combined into one option remain combined; the editor
  does not guess how to split them.
- Etsy offers no transaction across the whole batch. Updates are journaled and
  checked, but another Etsy client can change a listing between check and write.
- Restore is a new reviewed update, not a reversal of Etsy history or external
  fulfillment changes. Etsy-assigned identifiers may differ after restoration.
- Automatic sync defaults to 15 minutes and runs only while the local service is
  running. Actual shop size and API quota determine a suitable interval.
- Source code availability does not grant Etsy API access. Each installation
  needs its owner's approved credentials and authorized shop access.

After updating from a version that sorted inventory, sync the shop and prepare
a fresh review to read Etsy’s current option order. Existing reviews keep the
order they were prepared with. The app checks returned option order after saving;
live display in Etsy Shop Manager still needs verification on the connected PC.
