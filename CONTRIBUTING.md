# Contributions welcome

Please share useful fixes and improvements through a pull request when you can;
there is no obligation to do so. Describe the problem and relevant isolated tests.
Original project material is CC0. When submitting original contributions, state
whether you agree to make them available under CC0; flag other licensing/third-party
origins so maintainers can review them. Do not claim another contributor's rights.

Read docs/DEVELOPMENT.md and architecture/semantics first. Use synthetic data and disposable
rootless fixtures. Never commit real mail, credentials, certificates/private keys,
configuration dumps, recovery checkpoints or unredacted logs. Do not weaken identity,
transaction, namespace, source reconciliation or lifecycle safety to make a test pass.
A passing compile or happy path is not full qualification. Public preview has exact
platform/one-message limits; extra targets require separately evidenced qualification.
