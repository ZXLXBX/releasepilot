---
id: ISSUE-104
title: Export silently truncates files over 10 MB
labels: bug, data-loss
reported: 2026-11-11
---
`widgetkit export` on inputs larger than about 10 MB writes a truncated CSV
with no warning and exit code 0. Silent data loss; should either stream
correctly or fail loudly.
