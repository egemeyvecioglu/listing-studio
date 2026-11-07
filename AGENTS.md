# Listing Studio

Personal project owned by egemeyvecioglu. Do not use organization repositories,
company credentials, company code, or company infrastructure for this project.

## Updating an installed copy

- Read README.md and inspect the current checkout and running app before acting.
- If the user requests an update, fetch this repository and prefer the latest
  published stable release; until releases exist, use main. Use fast-forward-only
  updates for a clean checkout. Preserve local edits and stop on conflicts.
- An existing ZIP install is not a Git checkout: clone into a new folder and
  preserve/copy the existing runtime folder if present. Never delete the old
  installation until the new one starts successfully.
- Close the local app before replacing its files. Reopen the same launcher after
  the update and refresh the browser. Tell the user what version was installed.
- Preserve `%LOCALAPPDATA%\EtsyListingStudio`, `.local/`, browser drafts, the
  Windows certificate, and the existing runtime. Updates must not reset shop
  connections or require a new API registration unless a real migration needs it.
- Never print, upload, commit, or copy API keys, secrets, OAuth tokens, local
  databases, or certificates into this repository. Do not open credential-vault
  values to perform an update.
- Verify offline/sample behavior. Do not apply changes to real Etsy listings as
  an update test; real writes require the user's review and explicit approval.

## Development

- Keep the UI English; route new visible strings through `t`/`tr` and the locale
  files. Turkish translations will be added later.
- Use direct list and table editing: + to add, pencil to edit, trash to remove.
  Do not expose the internal rule builder as the everyday editing experience.
- Maintain shop isolation, full inventory preservation, review-before-write,
  conflict checks, interrupted-batch recovery, and explicit restore previews.
- Keep tests focused. Run `python check_edits.py` and `node --check web/app.js`
  for changes to the edit paths. For galleries, also run `python check_images.py`
  and `node --check web/images.js`. Distinguish sample checks from real Etsy tests.
- For grouped price changes, run `node check_prices.js` and `python check_edits.py`.
- For review or dependency changes, run `python check_review.py` and `node --check web/review.js`.
- For task coordination changes, run `python check_tasks.py` (local HTTP/sample only).
- Never commit or push changes unless the user requests publication.
