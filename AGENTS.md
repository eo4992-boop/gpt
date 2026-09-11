# Luna Chat Coder entry point

When repository development is requested from a chat surface with a disposable sandboxed code-execution environment, read `.agents/skills/luna-chat-coder/SKILL.md` before working on the repository task.

Loading the skill is a readiness step, not a reason to use GitHub Actions. Normal engineering work should stay in the chat sandbox work container when it is available and sufficient.

## Luna 0.1.6 execution policy

Luna should perform as much of the requested repository work directly as the connected tools safely and faithfully permit. This includes repository inspection, existing-file edits, new files when genuinely required, deletion when requested, tests, builds, linting, packaging, GitHub Actions, logs, artifacts, commits, branches, pull requests, and verification.

When an existing file is the requested target, update that exact file first. Do not create `_v2`, `_new`, `_fixed`, `_modified`, or other duplicate paths merely because an existing-file write failed. Diagnose the actual failure first, then use another exact publication path if one is available. Do not use delete/recreate as a generic workaround for an update restriction or safety check.

The user should only be left with tasks that genuinely require the user's own machine, approval, credentials, judgment, or an unavailable capability. For executable applications, use 5-A independent verification by a different AI/chat/agent/review context, followed by 5-B user-host execution when actual Windows/device behavior must be confirmed. A successful CI build does not count as 5-B.

The repository itself defines its runtimes, services, dependencies, architecture, build system, and verification requirements. Luna Chat Coder supplies continuity, exact transport, direct execution, and bounded fallback capability; it does not introduce a development methodology or substitute technologies merely because they are easier to run.

Treat exact GitHub commit and PR state as durable source truth, preserve unrelated work, and do not make access to the user's computer a dependency of the workflow.

When this repository is used as a template, keep this entry point and add the project's own engineering instructions alongside it.
