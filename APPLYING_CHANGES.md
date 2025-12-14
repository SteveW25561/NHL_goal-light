# Applying updates from a pull request

If you want to get the latest changes (like the auto-only lockout update) into your own checkout, use the workflow below.

1. **Make sure your local repo is clean**
   - Run `git status` and commit or stash anything listed so you have a clean working tree.

2. **Fetch the latest commits**
   - `git pull` brings down the current branch from the remote (e.g., `origin`).
   - If you work on a fork, run `git remote -v` to confirm the remote name and use it with `git pull <remote> <branch>`.

3. **Create a PR if you want to contribute back**
   - Open a new branch (e.g., `git checkout -b feature/auto-lockout`), make your changes, then commit them.
   - Push the branch (`git push -u origin feature/auto-lockout`) and open a pull request. Merging the PR updates the repository so everyone sees the new file contents.

4. **Deploy to your device**
   - After pulling the merged changes, restart the goal light service or rerun the script so it loads the updated code and version.

These steps keep your local copy in sync and ensure merged PRs update the repository for all users.
