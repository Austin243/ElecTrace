# Security policy

## Supported versions

ElecTrace is pre-release software. Security fixes are applied to the latest commit on
the default branch; no released version is currently promised long-term support.

| Version | Supported |
| --- | --- |
| Default branch | Yes |
| Older commits or forks | No |

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability or attach sensitive input
data to an issue.

Use GitHub's **Report a vulnerability** / private security advisory flow for this
repository. If that feature is unavailable, contact a repository maintainer through
an already established private channel and ask for a secure reporting route. Do not
send exploit details through a new public channel.

Include, when safe:

- affected revision and operating system;
- minimal reproduction and impact;
- whether untrusted files, paths, plugin output, or HTML reports are involved;
- any known workaround;
- your preferred disclosure and credit handling.

Maintainers will acknowledge a complete report as soon as practical, assess scope,
coordinate a fix, and agree on disclosure timing. Because this is a research project
without a guaranteed response-time service level, do not rely on it for isolation of
hostile workloads.

## Security model

ElecTrace reads calculation directories and external-tool output that may be
untrusted. Its adapters construct command plans containing an argument vector,
working directory, standard input, required inputs, and expected outputs. Constructing
a plan does not execute it. Callers that execute a plan remain responsible for
sandboxing and access control.

Security-sensitive areas include:

- shell metacharacters, path traversal, symlink escapes, and output overwrites;
- malicious or malformed VASP/plugin files causing denial of service;
- external executable substitution through `PATH` or configuration;
- credentials or private paths leaking into provenance and reports;
- HTML/script injection from frame labels or tool output;
- decompression bombs or unexpectedly large volumetric files;
- non-finite values and parser differentials at trust boundaries.

ElecTrace is not a sandbox. Do not execute plugins from an untrusted checkout, run
unknown external binaries, or analyze hostile data with credentials or valuable
write access available.

## Scientific integrity is different

Incorrect results, unstable matching, and misleading scientific interpretations are
important bugs. Use the scientific-validation issue template unless the behavior can
also cross a security/privacy boundary or enable malicious code execution.
