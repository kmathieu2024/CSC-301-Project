# Git Branching and Review Workflow

## Branching Strategy

Our team will use a feature-branch workflow. The `main` branch will contain reviewed and approved project code. Each team member will create a separate branch for their assigned tasks.

Branch naming conventions:

- `feature/` — New simulator functionality
- `docs/` — Documentation changes
- `test/` — Testing changes
- `fix/` — Bug fixes

## Development Process

1. Pull the latest changes from `main`.
2. Create a new branch for the assigned task.
3. Make changes and test locally.
4. Commit changes with a descriptive message.
5. Push the branch to GitHub.
6. Open a pull request into `main`.
7. Request a review from another team member.
8. Merge after approval and successful automated tests.

## Code Review Requirements

- At least one other team member must review each pull request.
- All automated tests must pass before merging.
- Changes must follow the team's coding standards.
- Merge conflicts must be resolved before merging.
- Team members should not approve their own pull requests.

## Commit Message Guidelines

Commit messages should clearly describe the changes made.
