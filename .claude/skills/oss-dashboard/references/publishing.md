# Before the dashboard code goes public

Every number the dashboard shows is public data except repository traffic (views, clones, referrers, popular
pages), which GitHub shows only to people with push access. Beyond that, the risk is what leaks *around* the code. Check each item before opening the PR on a public repository.

- [ ] **No personal accounts in code or fixtures.** Maintainer logins live in the `MAINTAINER_LOGINS` secret or
      the dashboard's settings table. Test fixtures use alice, bob and example.com.
- [ ] **No email addresses stored.** Commits without a linked account become "(no GitHub account)".
- [ ] **No traffic data anywhere public:** not in fixtures, tests, screenshots or PR text.
- [ ] **No internal figures in the PR body or commit messages.** A squash merge turns the PR body into the commit
      message, permanently. Screenshots with real numbers stay out of public threads.
- [ ] **No unreleased plans in docs.** Plan documents and issues get trimmed to what is being shipped; an issue's
      edit history stays visible after you edit it, so trim before posting, not after.
- [ ] **No secrets or account IDs.** `database_id`, the Access AUD tag and team domain are not secrets, but keep
      them as placeholders in the public repo if the deploy is done from a private fork or local edits.
- [ ] **No link to the dashboard from the public site.** Not for security (Access protects it), but a public
      link only leads people to a sign-in wall.
- [ ] **Branch history.** If early commits on the branch held a plan or data that was later removed, squash-merge
      so they never reach the default branch.
