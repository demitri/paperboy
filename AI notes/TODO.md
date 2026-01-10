# Paperboy - TODO & Progress Tracker

This document tracks todo items, progress, blockers, and open questions.

---

## Current Tasks

| Status | Task | Priority | Notes |
|--------|------|----------|-------|
| - | (No active tasks) | - | - |

**Status legend**: `[ ]` pending, `[~]` in progress, `[x]` completed, `[!]` blocked

---

## Open Questions

*Questions that need answers before proceeding:*

- (None currently)

---

## Blockers

*Issues preventing progress:*

- (None currently)

---

## Progress History

### 2026-01-08

**Project reorganization**
- Created `AI notes/` directory for AI agent documentation
- Created `index/` directory for indexing components
- Moved `arxiv_index.db` to `index/`
- Moved `index_arxiv_bulk_files.py` to `index/`
- Created this TODO tracker and START_HERE.md quick start guide

**Documentation update**
- Added "Updating the Index with New Tar Files" section to START_HERE.md
- Documents full directory scan and single-file indexing procedures
- Includes verification commands

---

## Completed Tasks

| Date | Task | Notes |
|------|------|-------|
| 2026-01-08 | Project reorganization | Created AI notes/, index/ directories; moved indexing files |
| 2026-01-08 | Document index update procedure | Added to START_HERE.md |

---

## Ideas / Future Improvements

*Captured ideas for potential future work:*

- (Add ideas here as they come up)

---

## Notes

- The `arxiv_index.db` is ~137 MB and indexes 884,524 papers
- Database is git-ignored; must be recreated or copied when setting up
- Index script uses MD5 hashing for deduplication
