<!-- ironclad:begin header -->
<!-- ironclad:end header -->

The Frappe app frappe-nix's Ironclad self-tests run against
(`docs/ironclad/spec.md` §1.3). It is not published anywhere.

It has one of each thing the platform checks: a DocType, a tested whitelisted
function and a deliberately untested one (exempted in `[[tool.ironclad.untested]]`),
`doc_events` and `extend_doctype_class` on ToDo, a scheduler job, an
`after_request` hook, a demo hook, a marketplace listing, an icon pair and a
screenshot spec. The files `ironclad sync` manages are committed by the sync
engine; everything else here is hand-written.

<!-- ironclad:begin compatibility -->
<!-- ironclad:end compatibility -->

<!-- ironclad:begin installation -->
<!-- ironclad:end installation -->

<!-- ironclad:begin support -->
<!-- ironclad:end support -->

<!-- ironclad:begin development -->
<!-- ironclad:end development -->

<!-- ironclad:begin license -->
<!-- ironclad:end license -->
