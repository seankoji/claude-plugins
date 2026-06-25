# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] - 2026-06-25

### Added

- `plugins/claude-tuneup` — Permission audit and settings tuneup packaged as a plugin.
  Commands: /claude-tuneup:claude-tuneup. Bundled: scripts/scan_perms.py.
- `plugins/prompt-builder` — Prompt engineering assistant packaged as a plugin.
  Commands: /prompt-builder:prompt-builder.
- `plugins/imps` — Swarm orchestrator packaged as a plugin.
  Commands: /imps:imps, /imps:status, /imps:prs, /imps:issue-mode.
  Bundled: scripts/imps-intro.py, 5 persona briefs.
- `.github/workflows/validate.yml` — CI that validates all plugin manifests, marketplace
  name-to-source consistency, script executability, and bundled-asset path hygiene.
- `schemas/plugin.schema.json` and `schemas/marketplace.schema.json` — JSON schemas.
- `elephant.md` — Durable design doc generated via /elephant-goldfish:elephant
  (dogfoods the flagship plugin against this repo).

### Changed

- `.claude-plugin/marketplace.json` name renamed from `claude-plugins` → `seankoji`
  (avoids CLI rejection of names containing "claude").
- `AGENTS.md` "Validate" section updated to reference CI; manual check condensed to
  a quick one-liner.

### Removed

- Top-level `commands/`, `scripts/`, `personas/` directories. All content migrated
  into the respective plugin packages under `plugins/<name>/`.
