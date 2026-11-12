# RobustNotes for Anna

The Anna edition is a responsive web workspace built separately from the RobustNotes Windows app. It brings the main notebook, whiteboard, and text-file workflows into Anna’s sandbox rather than trying to run the PySide desktop app in a browser.

## Features

- **One workspace:** create notebooks, whiteboards, and text/Markdown/code files; search titles, tags, folders, and content; favorite, rename, duplicate, tag, organize, and delete items.
- **Notebook canvas:** smooth pointer and stylus ink, pen colors and width, highlighter, glowing laser, eraser, line and geometric shapes, freehand/rectangle lasso selection, movable objects, and an image resize handle.
- **Pages and paper:** add, duplicate, clear, rename, and reorder pages; Dot grid, ruled, Cornell, meeting, and weekly planner templates; fit-to-width zoom starts near 84%, with slider and typed percentage.
- **Text and images:** editable text boxes on the canvas, rich text in text files, per-text color/size, image insertion from a file or clipboard, PNG export, and browser print-to-PDF for notebook pages.
- **PDF attachments:** add a PDF to a notebook and reopen it from Anna storage. Canvas annotations are saved as page ink.
- **AI and study:** optional RobustNotes AI sidebar uses Anna’s selected model for page-aware questions, handwriting OCR, and study-card drafts. OCR offers to replace selected handwriting with editable cursive-style text. A bundled deterministic tool reports word count, read time, and Markdown headings.
- **Voice input:** browser speech-to-text where supported.
- **Backup and recovery:** JSON workspace export/import, optional AES-GCM password encryption, and a small rolling set of manual document revisions.
- **Convenience:** keyboard shortcuts, quick calculator, ruler/scale, compass, and local audio playback with queue controls.

## Important differences from the Windows app

Anna apps run in a web sandbox, so some desktop behavior has a different boundary:

- Notes are stored in Anna’s private per-account app storage, not in the Windows device profile or desktop workspace. Anna handles updates through its app platform; this edition does not use the Windows self-updater.
- Handwriting OCR and AI questions send the selected page snapshot and/or note text to Anna’s selected model after the user chooses the action. This does not run the desktop TrOCR model locally.
- PDF files can be attached, stored, and opened for reference. The browser edition does not yet render imported PDF pages as the canvas background or extract their selectable text.
- Study Music plays files the user selects in the browser. The desktop Funkwhale/Subsonic remote-library integration is not included because Anna’s web sandbox cannot connect safely to arbitrary user-selected pod origins yet.
- Anna provides account identity in place of desktop local profiles. App updates and review are handled by Anna.

The first Anna version’s simple notes are migrated on first open into the new workspace format. The Anna KV entry is capped in the UI to 200 KB; large media should remain in attachment storage, and users should export a backup before major changes.

## Local development

Requirements: Node.js 22+, `uv`, and the Anna CLI.

```powershell
npm install --global @anna-ai/cli
anna-app login --host https://anna.partners
anna-app dev --cwd .\robustnotes-anna
```

Open the URL printed by `anna-app dev`. Anna’s SDK, persistent storage, and model APIs are available when opened in the Anna harness. The app bundle itself is in `bundle/`.

## Validation and publication

```powershell
Set-Location .\robustnotes-anna
anna-app validate --strict
anna-app apps publish
anna-app apps submit-review robustnotes-anna
```

The Anna CLI publishes the app and bundled Executa, then cuts an immutable version. Complete the developer profile setup if prompted. Public release waits for Anna’s review; after approval, release the reviewed version with:

```powershell
anna-app apps release <approved-version> --slug robustnotes-anna
```
