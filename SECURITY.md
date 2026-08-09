# Security policy

Security fixes target the latest default-branch revision; this pre-release does not
promise long-term support for older versions.

## Reporting

Do not open a public issue or attach sensitive data. Use GitHub's **Report a
vulnerability** flow, or ask a maintainer through an established private channel for
a secure reporting route. Include the affected revision, a minimal reproduction,
impact, and any known workaround when it is safe to do so.

## Scope

ElecTrace reads external calculation files and can launch explicitly authorized
executables. Planning does not execute commands; the workflow `--yes` boundary does.
ElecTrace is not a sandbox, so do not run unknown binaries or process hostile data
with valuable credentials or write access available.

Relevant issues include path traversal, symlink escapes, unsafe overwrites, command
argument handling, executable substitution, denial of service from malformed inputs,
and sensitive data leaking into provenance or reports.

Incorrect scientific results are important bugs, but they are security issues only
when they also cross a confidentiality, integrity, or execution boundary.
