# RobustNotes for Anna

A lightweight, web-native notes companion designed for Anna’s sandboxed app platform. It is a separate edition from the RobustNotes Windows, Android, and desktop apps.

## Included in this first release

- Create, edit, search, and delete notes.
- Autosave notes to Anna Persistent Storage (APS), in this app’s private `app` scope.
- Deterministic note insights: word count, estimated reading time, and Markdown heading extraction through a bundled Executa.
- Export a JSON backup at any time.
- Responsive dark interface with a compact notes list and focused editor.

Notes are isolated by Anna per account and can be available on other devices using that same Anna account. This edition does **not** read or sync files from the desktop app. It does not send notes to a model or third-party service. The standalone preview uses browser local storage and is not Anna account storage.

The app enforces a 200 KB total APS value limit and a 40-note limit so autosave remains within small key-value storage. Export a backup before deleting notes or moving between editions.

## Local development

Requirements: Node.js 22+, `uv`, and the Anna CLI.

```powershell
npm install --global @anna-ai/cli
uv --version
anna-app login --host https://anna.partners
anna-app validate --strict --cwd .\robustnotes-anna
anna-app dev --cwd .\robustnotes-anna
```

Open the development URL printed by the CLI. The app must run inside Anna’s harness for the Anna SDK and account storage to work. If `uv` is missing, install it using the official Astral instructions before starting the harness.

## Publishing

```powershell
anna-app apps publish --cwd .\robustnotes-anna
```

The CLI publishes the bundled Executa and app draft/version. Complete Anna’s developer profile setup (including a developer handle) if prompted, then review the draft in the Anna Developer Hub and submit it for review. Public distribution requires Anna’s approval; a successful upload alone does not mean the app is publicly listed.

## Project layout

- `bundle/` — static HTML, CSS, and JavaScript user interface.
- `executas/robustnotes-anna/` — small Python Executa for note insights.
- `manifest.json` — Anna app and view configuration.
- `app.json` — store metadata and bundled Executa mapping.
