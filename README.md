# fpp-data

This repository is the data backing FPP's Plugin Manager:

- **`pluginList.json`** - the master index of community plugins. Each entry
  points at a plugin's `pluginInfo.json` (hosted in the plugin's own repo),
  which FPP fetches to list, version-check, and install it.
- **`pluginCategories.json`** - the canonical category list plugins can tag
  themselves with.

## Submitting a plugin

See **[CONTRIBUTING.md](CONTRIBUTING.md)** for the full guide - pre-submission
guidelines, the `pluginInfo.json` contract, the `pluginList.json` entry
format, and what CI checks automatically. Two ways in:

1. **[Submit a plugin](https://darylc.github.io/fpp-data-ci/submit_new_plugin/)**
   (Issue Form, no git needed) - the guided page fills in the form for you and
   encrypts your contact email.
2. **Pull Request** - add your entry to `pluginList.json` directly; a CI
   check validates it and comments on the PR.

Building the plugin itself? Start from
[fpp-plugin-Template](https://github.com/FalconChristmas/fpp-plugin-Template)
- it has the `pluginInfo.json` format reference, plugin guidelines, and a
working skeleton to fork.

## Removing a plugin

Start at the [**guided removal page**](https://darylc.github.io/fpp-data-ci/submit_remove_plugin/),
which fills in the repoName for you (no email involved here, so it's a single step,
unlike the submission flow above). Existing installs are unaffected; the entry is just
removed from `pluginList.json`.
