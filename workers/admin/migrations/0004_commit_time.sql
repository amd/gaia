-- Full commit timestamp, so "last 24 hours" counts are exact rather than by day.
ALTER TABLE commits ADD COLUMN at TEXT;
