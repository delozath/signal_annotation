# Official sources consulted

Reviewed on October 2, 2026. Dependencies used for testing are pinned in
`requirements-tested.txt`; pages under `latest`/`main` may change.

- Official bioread demo: reading channels, values, units, and differing sampling rates.
  https://uwmadison-chm.github.io/bioread/bioread_quick_demo.html
- bioread API: `read`; `read_file` is retained as a compatibility alias.
  https://github.com/uwmadison-chm/bioread/blob/main/bioread/__init__.py
- Channel, scaling, and `time_index` implementation.
  https://github.com/uwmadison-chm/bioread/blob/main/bioread/biopac.py
  This implementation was also inspected in the installed bioread 3.1.0 package.
- Reader notes and limitations: channel ordering and untested older versions.
  https://github.com/uwmadison-chm/bioread#notes
- ACQ format and reduced per-channel sampling rate.
  https://github.com/uwmadison-chm/bioread/blob/main/notes/file_format.md
- Bokeh: synchronization using shared ranges.
  https://docs.bokeh.org/en/latest/docs/user_guide/interaction/linking.html
- Bokeh: applications and server callbacks.
  https://docs.bokeh.org/en/latest/docs/user_guide/server/app.html
- Bokeh Server API and programmatic execution.
  https://docs.bokeh.org/en/latest/docs/reference/server/server.html
- Security/origins and network configuration.
  https://docs.bokeh.org/en/latest/docs/user_guide/server/deploy.html#security
  https://docs.bokeh.org/en/latest/docs/reference/command/subcommands/serve.html#network-configuration

These sources document the APIs; they do not replace testing with the user's specific
ACQ files. No example clinical recordings were downloaded.
