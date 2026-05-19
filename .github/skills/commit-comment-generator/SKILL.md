---
name: commit-comment-generator
description: 'Generate short, to-the-point commit messages from a git diff. Use when the user asks to generate a commit message, write commit notes, summarize changes for a commit, or describe what was changed.'
argument-hint: 'Optional: branch or file path to diff against'
---

# Commit Comment Generator

Generate concise, meaningful commit messages by analyzing staged or unstaged git diffs.

## When to Use

- User asks to "generate a commit message" or "write commit notes"
- User asks "what should my commit say?" or "summarize my changes"
- User wants to describe their changes before committing

## Procedure

1. **Get the diff**: Run `git diff --staged` for staged changes. If nothing is staged, fall back to `git diff HEAD` for all uncommitted changes.

2. **Analyze the diff**: Identify:
   - What files changed (and in what areas/modules)
   - What was added, removed, or modified
   - The intent behind the changes

3. **Write the commit message** using this format:
   ```
   <type>: <short summary (50 chars max)>

   - <bullet point if more detail is needed>
   - <keep to 2-3 bullets max>
   ```

   **Types**: `feat`, `fix`, `refactor`, `docs`, `chore`, `test`, `style`

4. **Rules for the message**:
   - First line: imperative mood, no period, ≤50 chars
   - Be specific: say *what* changed, not just *that* it changed
   - Skip obvious filler ("update file", "make changes")
   - Only add bullet points if the one-liner would lose important context
   - Do NOT include Co-authored-by or other trailers unless asked

5. **Present** the commit message in a code block so it's easy to copy.

## Example Output

```
feat: add CTRE swerve drive command factory

- Wraps SwerveRequest types for field-relative control
- Exposes builder methods for max speed configuration
```
