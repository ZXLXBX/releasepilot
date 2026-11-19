---
id: ISSUE-101
title: Crash on startup when config file is missing
labels: bug, crash
reported: 2026-11-02
---
Running `widgetkit` in a fresh directory with no config file raises
FileNotFoundError and exits with a traceback instead of creating a default
config. This blocks every new user on first run.
