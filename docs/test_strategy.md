# Testing Strategy

The OS Simulator uses pytest for automated testing.

## Milestone 1
Initial tests verify that baseline input files exist,
contain data, and that process JSON is valid.

## Future Milestones
Unit tests will verify individual manager functions.
Integration tests will verify interactions between managers.
Baseline tests will compare actual simulator output
against documented expected results.

## Running Tests
python -m pytest tests/ -v

## Continuous Integration
GitHub Actions runs automated tests on every push
and pull request.

## Merge Requirements
Tests must pass and at least one teammate must
approve the pull request before merging.