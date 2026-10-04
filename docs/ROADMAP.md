# Incremental roadmap

1. **Validate a representative ACQ file.** Compare channels, sampling rates, samples,
   units, amplitudes, and times against AcqKnowledge; include heterogeneous sampling and
   compression if they are used. The current tests do not replace this step.
2. **Pilot the manual UI.** On Windows/macOS and the usual browser, test clicking,
   dragging, editing, closing/reopening, and permission messages. Keep the adjacent JSON
   as the current state.
3. **Edit the agreed schema.** Add graphical tools for regions, recording annotations,
   metadata, and relationships. Preserve IDs and reject broken references. There is no
   need to introduce a database or history.
4. **Proposal review.** Add an inbox with distinct styles and accept/reject actions. The
   port and use cases already separate proposals from labels. Use a synthetic provider
   in tests before connecting any model.
5. **First local model, if chosen.** Define labels, authorized data, metrics, and a
   validation set. Version the model as proposal metadata; do not enable data transfer
   or automatic acceptance. The human decision remains explicit.
6. **Distribution based on the pilot.** Provide per-system installers, browser testing,
   and performance testing with real recordings. Clinical use would require a dedicated
   risk-management, validation, and compliance project.

The previous version is not migrated automatically. If old data is provided later, an
explicit, verifiable conversion will be designed without deleting the originals.
