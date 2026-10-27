# Review: Changes Since Last Commit

Compared `HEAD` (`19d3051`) with its first parent (`5b43955`).

## Findings

### [P1] Inline review comments cannot be posted with read-only PR permissions

`.github/workflows/claude-code-review.yml:19-22` grants `pull-requests: read`, while the updated prompt at line 41 asks the review plugin to post inline comments. The plugin's comment tool needs write access, so reviews that find issues cannot publish their inline comments. Grant `pull-requests: write` to this job (and only the permissions otherwise needed).

### [P1] The allowlist blocks the review plugin's required `gh` commands

`.github/workflows/claude-code-review.yml:42` allows only `mcp__github_inline_comment__create_inline_comment`. The configured code-review plugin uses `gh pr view`, `gh pr diff`, `gh issue view`, and related `gh` commands to inspect and check the PR; those tools are not included in this explicit `claude_args` allowlist. The action can start but Claude cannot complete the review. Add the plugin's required `Bash(gh ...)` tools to the allowlist.

## Scope

Reviewed the two workflow changes in the latest merge commit. No tests were run; this change only modifies GitHub Actions configuration.
