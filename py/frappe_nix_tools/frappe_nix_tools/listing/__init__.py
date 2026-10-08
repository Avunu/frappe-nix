"""``frappe-nix listing``: registry readiness, publishing and the README blocks (spec §2.19, §2.21, §5.2).

- ``rules``: the L1 to L12 checks of ``frappe-listing check``;
- ``baseline``: the multiset semgrep baseline (S21) and L7;
- ``getapp``: L9, pilot's get-app validator on a bench with the app's dependency apps;
- ``registry``: ``frappe-listing registry``, the registry pull request;
- ``readme``: the README generated blocks, rendered by the sync engine (``handler: readme``);
- ``target``: the app a command looks at, resolved once.

Every organisation value (publisher, e-mail, licence, URL templates, registry fork) comes
from the app's resolved configuration (S37), never from a constant here.
"""
