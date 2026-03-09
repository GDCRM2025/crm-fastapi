FullCalendar vendor files (avoid CDNs)

Required on server:
- index.global.min.js

FullCalendar 6 "global" bundle injects CSS at runtime, so a separate .css file may not exist.

Example (run on server from CRM root):
  mkdir -p web/vendor/fullcalendar/6.1.11
  curl -fsSL -o web/vendor/fullcalendar/6.1.11/index.global.min.js https://cdn.jsdelivr.net/npm/fullcalendar@6.1.11/index.global.min.js

