# Coding Standards

## Language
All simulator components will be written in Python 3.11+.

## Naming Conventions
- Variables: snake_case
- Functions: snake_case
- Classes: PascalCase
- Constants: UPPER_CASE

## Code Formatting
- Use 4 spaces for indentation.
- Keep lines to approximately 88 characters.
- Use descriptive variable and function names.
- Add docstrings to public functions and classes.
- Use type hints for function parameters and return values.

## Code Organization
- Keep each manager in its assigned directory.
- Avoid duplicating functionality across managers.
- Shared functionality belongs in src/core/.
- Keep simulation logic separate from dashboard code.

## Error Handling
- Validate inputs before processing them.
- Raise appropriate exceptions for invalid input.
- Avoid silently ignoring errors.

## Testing
- New functionality must include relevant tests.
- Run all automated tests before submitting a PR.
- Fix failing tests before requesting a merge.

## Code Reviews
- At least one teammate must review each PR.
- Do not merge code that fails automated tests.