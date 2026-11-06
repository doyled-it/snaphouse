"""snaphouse: the small bits of logic behind the CLI and the Ansible roles.

Most of the system is Ansible configuration. This package holds the pieces that
are not configuration: the self-update wrapper, the verify runner, the metadata
bridge, and the client and room-picker models. Each lands with its own task and
tests.
"""
