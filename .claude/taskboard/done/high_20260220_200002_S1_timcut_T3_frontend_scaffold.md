REQUESTED_BY: S1
TASK: Build the complete React + Tailwind frontend scaffold for TimCut. This is a professional dark-theme video production UI modeled after RunwayML / DaVinci Resolve.

Project location: /home/darren/Desktop/timcut/frontend/

Setup:
- React 18 + Vite + Tailwind CSS
- Create package.json, vite.config.js, tailwind.config.js, postcss.config.js, index.html
- Create Dockerfile (nginx serving the built SPA, proxying /api to backend:8000)
- nginx.conf for the frontend container

Layout (all dark theme — near-black bg #0a0a0f, subtle borders #1a1a2e, accent blue #3b82f6):
- Top bar: "TimCut" logo/text, project name, save button, export button, settings icon
- Left sidebar (240px): Asset library — grid of thumbnails with filenames, upload button at top
- Center (flex-grow): Preview player — HTML5 video element with transport controls (play/pause, seek bar, timestamp, volume)
- Bottom (200px): Timeline — horizontal track with clip thumbnails, trim handles (drag), clip reordering (drag & drop)
- Right sidebar (280px): Properties panel — tabbed (Filters / Adjustments / Export), form controls for current selection

Pages/Routes:
- / — Main editor workspace (the layout above)
- /login — Simple password input form (HTTP Basic Auth)

Components to create:
- Layout.jsx — The 4-panel layout shell
- TopBar.jsx — Project info + actions
- AssetLibrary.jsx — Left sidebar, grid view, upload dropzone
- PreviewPlayer.jsx — Video player + transport controls
- Timeline.jsx — Bottom panel, clip track, trim handles
- PropertiesPanel.jsx — Right sidebar, tabbed interface
- LoginPage.jsx — Password gate
- UploadDropzone.jsx — Drag & drop file upload with progress
- JobStatus.jsx — Processing indicator (spinning, progress %, cancel button)

Styling requirements:
- Professional look — NOT a hackathon project
- Smooth transitions, hover states, proper spacing
- Monospace font for timestamps/technical info
- System font stack for UI text
- Responsive enough to work on 1920x1080 and 1440x900

Do NOT connect to backend APIs yet — use placeholder data / mock state. The goal is the complete UI shell that looks production-ready.

SCOPE: /home/darren/Desktop/timcut/frontend/
OUTPUT_TO: All files in frontend/ directory
PRIORITY: high
CONSTRAINT: Do NOT run npm install or any build commands. Write all source files only.
AGENT_TYPE: analysis
DEPENDS_ON: none
CLAIMED_BY: S1
CLAIMED_AT: 2026-02-20T23:07:24Z
COMPLETED_AT: 2026-02-20T23:18:39Z
RESULT: All files written successfully
