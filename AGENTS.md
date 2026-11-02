# Repository workflow

- Read CONTRIBUTING.md before changing this repository.
- Start each new development task on a new feature/*, fix/* or docs/* branch from current main; do not develop directly on main or reuse the historical feature/p1-contracts branch.
- Keep backend and frontend paired by release tag. Contract changes require synchronized generated files and checks in both repositories.
- Run relevant checks and obtain review before merging. Follow the user's explicit authorization for external pushes, merges and releases.
- Never move published version tags or force-push main. Keep raw robot data, USD assets, local environments and secrets outside Git.
- Remote robot and data-server files are read-only unless the user explicitly changes that instruction.
