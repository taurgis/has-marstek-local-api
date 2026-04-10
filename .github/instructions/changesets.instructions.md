---
description: 'Require a changeset for every releasable change'
applyTo: '**'
---

# Changeset Requirement

## Rule
- Every task that adds, modifies, or removes code, configuration, entities, services, translations, or documentation **must** include a changeset file unless it falls under an explicit exemption.
- If pending changesets already exist in `.changeset/`, evaluate whether the current change is covered. If not, create an additional changeset.

## How to Create a Changeset
- Create a Markdown file in `.changeset/` with a kebab-case name (e.g., `.changeset/fix-battery-idle.md`).
- Use the following format:

```markdown
---
"ha-marstek-release-tools": patch
---

Short, user-facing summary of the change.
```

- Set the bump type to `patch` (bug fixes, maintenance), `minor` (new features), or `major` (breaking changes).
- Write the summary as a single sentence starting with a verb: "Add …", "Fix …", "Remove …", "Change …".
- Consult the `changesets` skill for detailed guidance on bump types and summary style.

## Exemptions (No Changeset Needed)
- Purely editorial fixes to comments or docstrings with no behavior change.
- Changes that only affect CI workflows, dev tooling, or test infrastructure with no user-visible impact.
- The automated **Release** PR opened by the Changesets GitHub Action.

## Multiple Changes in One Task
- Create one changeset per logical change. A task that fixes a bug **and** adds a feature should produce two changeset files.

## Updating an Existing Changeset
- If a follow-up edit refines a change that already has a pending changeset, update the existing changeset summary rather than adding a duplicate.

## Verification
- Before considering a task complete, confirm that at least one `.changeset/*.md` file (excluding `README.md` and `config.json`) exists for the current change, or that an exemption applies.
