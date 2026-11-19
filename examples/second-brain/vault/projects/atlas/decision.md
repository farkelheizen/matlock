---
title: Storage decision
status: accepted
content_type: decision
date: 2026-03-12
project_id: null
tags:
  - architecture
  - storage
---

# Storage decision

## Context

The Atlas project needs a simple way to track its migration notes.

## Decision

Keep the first iteration in a local SQLite database. Revisit the choice after
the team has measured the size and frequency of updates.
