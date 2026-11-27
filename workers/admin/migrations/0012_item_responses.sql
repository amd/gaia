-- The earliest comment each person left on each issue or pull request (conversation and inline
-- review comments, bots left out), with their association on that comment. It times the core
-- team's first response to outside contributors.
CREATE TABLE item_responses (
  number INTEGER NOT NULL,
  login  TEXT NOT NULL,
  assoc  TEXT NOT NULL,
  at     TEXT NOT NULL,
  PRIMARY KEY (number, login)
);
