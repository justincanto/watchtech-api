"""
Status enums for the application.

This module is intentionally kept dependency-free to avoid circular imports.
Both models.py and utility modules can safely import from here.
"""
import enum


class SourceType(enum.Enum):
    YOUTUBE = "youtube"
    MEDIUM = "medium"
    DEV_TO = "dev_to"


class SourceStatus(enum.Enum):
    """Status of source processing in the task queue."""
    PENDING = "pending"              # Source created, task not started
    FETCHING_AUTHOR = "fetching_author"  # Fetching author/channel data
    INGESTING_CONTENT = "ingesting_content"  # Ingesting initial content
    COMPLETED = "completed"          # Processing finished successfully
    FAILED = "failed"                # Processing failed


class ContentStatus(enum.Enum):
    """Status of content processing in the task queue."""
    PENDING = "pending"      # Task queued, not started
    EXTRACTING = "extracting"  # Extracting data from source
    SUMMARIZING = "summarizing"  # Generating AI summary
    COMPLETED = "completed"  # Processing finished successfully
    FAILED = "failed"        # Processing failed
    IGNORED = "ignored"      # Content intentionally skipped (e.g. non-video)

