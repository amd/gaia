-- Settings edited from the dashboard. 'maintainers' holds the core team
-- ("alice+alice-work,bob"); while unset, MAINTAINER_LOGINS from the deploy is used.
CREATE TABLE settings (
  key        TEXT PRIMARY KEY,
  value      TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  updated_by TEXT NOT NULL
);
