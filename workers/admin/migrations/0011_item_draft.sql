-- 1 while a pull request is a draft, 0 when it is not, as of its last collection;
-- NULL for issues, and for pull requests not collected since this column was added.
ALTER TABLE items ADD COLUMN draft INTEGER;
