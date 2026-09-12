"""Turning a brief into a branch the owner can read.

Nothing here edits the running deployment. Every change is built in a
separate git worktree on its own branch, tested there, and left as a diff
waiting for a decision. Pushing and merging are absent from this package
rather than guarded -- see app/dev/repo.py.
"""
