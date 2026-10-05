"""Driving adapter: browser UI. All durable changes go through use cases."""
from html import escape
from pathlib import Path
import numpy as np
from bokeh.events import SelectionGeometry, Tap
from bokeh.layouts import column, row
from bokeh.models import (BoxSelectTool, Button, ColumnDataSource, Dialog, Div,
                          CustomJS, HoverTool, PointDrawTool, Range1d, Select,
                          RangeSlider, Spinner, TapTool, TextAreaInput, TextInput)
from bokeh.plotting import figure
from ..application import AnnotationService, interval_color, point_colors
from ..validation import amplitude_label_catalog, general_notes, interval_label_catalog
from .display import visible_samples
from .storage import export_annotations_csv

INTERVAL_EDGE_HIT_WIDTH = 12


class Viewer:
    def __init__(self, doc, service: AnnotationService, sidecar: Path):
        self.doc, self.service, self.sidecar = doc, service, Path(sidecar)
        self.recording = service.recording
        self.active_channel_id = self.recording.channels[0].id
        self.pending_interval_start = None
        self.pending_refresh = None
        self.syncing_viewport = False
        self.syncing_annotation_selection = False
        self.refreshing_points = False
        self.refreshing_interval_handles = False
        self.plots, self.sources = {}, {}
        self.pan_tools, self.point_draw_tools, self.point_tap_tools = {}, {}, {}
        self.interval_renderers = {}
        self.interval_handle_sources, self.interval_draw_tools = {}, {}
        self.interval_edge_draw_tools = {}
        self.x_range = Range1d(0, min(8, self.recording.duration), bounds=(0, self.recording.duration))
        self.status = Div(text="Ready. Every change is saved automatically on this computer.", name="status",
                          styles={"color": "#31565b", "padding": "10px", "background-color": "#eef7f4"})
        self.mode = Select(title="Action", value="navigate", name="mode", options=[
            ("navigate", "Navigate"), ("marker", "Mark amplitude marker"),
            ("interval", "Mark interval")])
        self.amplitude_label_name = TextInput(title="New amplitude label", value="", max_length=200,
                                              name="amplitude_label_name")
        self.amplitude_label_select = Select(title="Amplitude label", value="",
            name="amplitude_label_select", options=self.amplitude_label_options())
        add_amplitude_label = Button(label="Add amplitude label", name="add_amplitude_label")
        add_amplitude_label.on_click(lambda: self.run(self.create_amplitude_label))
        self.general_notes = TextAreaInput(title="General recording notes", value="", rows=6,
                                           name="general_notes")
        save_general_notes = Button(label="Save general notes", name="save_general_notes")
        save_general_notes.on_click(lambda: self.run(self.save_general_notes))
        clear_general_notes = Button(label="Clear general notes", name="clear_general_notes")
        clear_general_notes.on_click(lambda: self.run(self.clear_general_notes))
        self.interval_label_name = TextInput(title="New interval label", value="", max_length=200,
                                             name="interval_label_name")
        self.interval_label_select = Select(title="Interval label", value="", name="interval_label_select",
                                            options=self.interval_label_options())
        self.interval_label_select.on_change("value", self.interval_label_changed)
        add_interval_label = Button(label="Add interval label", name="add_interval_label")
        add_interval_label.on_click(lambda: self.run(self.create_interval_label))
        self.start = Spinner(title="Time / start (s)", value=0, low=0, high=self.recording.duration,
                             step=.001, format="0.000000", name="start")
        self.end = Spinner(title="Interval end (s)", value=min(1, self.recording.duration), low=0,
                           high=self.recording.duration, step=.001, format="0.000000", name="end")
        add = Button(label="Add with these times", button_type="primary", name="add")
        add.on_click(lambda: self.run(self.add_numeric))
        self.view_start = Spinner(title="View start (s)", value=0, low=0, high=self.recording.duration, step=1, name="view_start")
        self.view_width = Spinner(title="Window (s)", value=min(8, self.recording.duration), low=.01,
                                  high=self.recording.duration, step=1, name="view_width")
        self.time_scrollbar = RangeSlider(title="Time navigation (s)", start=0,
                                           end=self.recording.duration,
                                           value=(self.x_range.start, self.x_range.end), step=.001,
                                           name="time_scrollbar", sizing_mode="stretch_width")
        go = Button(label="Go to window", name="go")
        go.on_click(lambda: self.run(self.go_window))
        reset = Button(label="View full recording", name="full")
        reset.on_click(self.full_view)
        self.assign_marker_label = Button(label="Assign selected amplitude label",
                                          name="assign_marker_label")
        self.assign_marker_label.on_click(lambda: self.run(self.reassign_selected_point_label))
        reload_button = Button(label="Reload saved state", name="reload")
        reload_button.on_click(lambda: self.run(self.service.reload))
        export = Button(label="Export annotation CSV", button_type="success", name="export")
        export.on_click(self.export)
        for ch in self.recording.channels:
            self.make_plot(ch)
        self.delete_events = ColumnDataSource(data={"sequence": [0], "selected_id": [""],
                                                     "selected_count": [0]},
                                              name="point_delete_events")
        self.delete_events.on_change("data", self.delete_selected_annotation_event)
        channel_ids = [channel.id for channel in self.recording.channels]
        point_sources = [self.sources[channel_id][1] for channel_id in channel_ids]
        interval_sources = [self.sources[channel_id][2] for channel_id in channel_ids]
        point_plots = [self.plots[channel_id] for channel_id in channel_ids]
        point_draw_tools = [self.point_draw_tools[channel_id] for channel_id in channel_ids]
        start_sources = [self.interval_handle_sources[channel_id]["start"]
                         for channel_id in channel_ids]
        end_sources = [self.interval_handle_sources[channel_id]["end"]
                       for channel_id in channel_ids]
        edge_draw_tools = [self.interval_edge_draw_tools[channel_id]
                           for channel_id in channel_ids]
        point_tap_tools = [self.point_tap_tools[channel_id] for channel_id in channel_ids]
        pan_tools = [self.pan_tools[channel_id] for channel_id in channel_ids]
        interval_boxes = [self.sources[channel_id][3] for channel_id in channel_ids]
        selected_point_interaction = CustomJS(args={
            "trigger": self.delete_events,
            "point_sources": point_sources,
            "interval_sources": interval_sources,
            "point_plots": point_plots,
            "point_draw_tools": point_draw_tools,
            "start_sources": start_sources,
            "end_sources": end_sources,
            "edge_draw_tools": edge_draw_tools,
            "point_tap_tools": point_tap_tools,
            "pan_tools": pan_tools,
            "interval_boxes": interval_boxes,
            "mode": self.mode,
        }, code="""
            const is_navigate = mode.value === "navigate"
            const use_interval_box = mode.value === "interval"
            const selection_key = `signal-annotation-selection-${trigger.id}`
            if (is_navigate && !window[selection_key]) {
                window[selection_key] = true
                try {
                    const all_handle_sources = [...start_sources, ...end_sources]
                    const selected_handle = all_handle_sources.find(
                        (source) => source.selected.indices.length > 0)
                    const point_source = point_sources.find(
                        (source) => source.selected === cb_obj)
                    const interval_source = interval_sources.find(
                        (source) => source.selected === cb_obj)
                    const handle_source = all_handle_sources.find(
                        (source) => source.selected === cb_obj)
                    const winning_handle = handle_source != null && cb_obj.indices.length > 0
                        ? handle_source : selected_handle
                    if (winning_handle != null) {
                        const index = winning_handle.selected.indices[
                            winning_handle.selected.indices.length - 1]
                        for (const source of point_sources)
                            source.selected.indices = []
                        for (const source of interval_sources)
                            source.selected.indices = []
                        for (const source of all_handle_sources)
                            source.selected.indices = source === winning_handle ? [index] : []
                    } else if (point_source != null && cb_obj.indices.length > 0) {
                        const index = cb_obj.indices[cb_obj.indices.length - 1]
                        for (const source of point_sources) {
                            source.selected.indices = source === point_source ? [index] : []
                        }
                        for (const source of interval_sources)
                            source.selected.indices = []
                        for (const source of all_handle_sources)
                            source.selected.indices = []
                    } else if (interval_source != null && cb_obj.indices.length > 0) {
                        const index = cb_obj.indices[cb_obj.indices.length - 1]
                        for (const source of point_sources)
                            source.selected.indices = []
                        for (const source of interval_sources)
                            source.selected.indices = source === interval_source ? [index] : []
                        for (const source of all_handle_sources)
                            source.selected.indices = []
                    }
                } finally {
                    window[selection_key] = false
                }
            }
            for (let i = 0; i < point_sources.length; i++) {
                if (!is_navigate && point_sources[i].selected.indices.length > 0)
                    point_sources[i].selected.indices = []
                if (!is_navigate && interval_sources[i].selected.indices.length > 0)
                    interval_sources[i].selected.indices = []
                if (!is_navigate && start_sources[i].selected.indices.length > 0)
                    start_sources[i].selected.indices = []
                if (!is_navigate && end_sources[i].selected.indices.length > 0)
                    end_sources[i].selected.indices = []
                const indices = point_sources[i].selected.indices
                const has_one_point = indices.length === 1 && Number.isInteger(indices[0]) &&
                    indices[0] >= 0 && indices[0] < point_sources[i].data.id.length
                const can_move_point = is_navigate && has_one_point
                const has_visible_edge = start_sources[i].data.id.length > 0 ||
                    end_sources[i].data.id.length > 0
                const active_drag = can_move_point ? point_draw_tools[i]
                    : (is_navigate && has_visible_edge ? edge_draw_tools[i] : null)
                point_draw_tools[i].visible = is_navigate
                edge_draw_tools[i].visible = is_navigate
                point_plots[i].toolbar.active_tap = can_move_point
                    ? point_draw_tools[i] : point_tap_tools[i]
                point_plots[i].toolbar.active_drag = active_drag != null
                    ? active_drag
                    : (use_interval_box ? interval_boxes[i] : pan_tools[i])
            }

            const has_selected_annotation = [...point_sources, ...interval_sources]
                .some((source) => source.selected.indices.length > 0)
            const active = document.activeElement
            if (has_selected_annotation && active != null && typeof active.blur === "function")
                active.blur()

            const key = `signal-annotation-delete-${trigger.id}`
            if (window[key] != null)
                document.removeEventListener("keydown", window[key], true)
            window[key] = (event) => {
                const target = event.target
                const tag = target?.tagName?.toLowerCase()
                const isDelete = event.key === "Delete" || event.code === "Delete"
                if (!isDelete || tag === "input" || tag === "textarea" ||
                        tag === "select" || target?.isContentEditable)
                    return
                event.preventDefault()
                event.stopPropagation()
                const sequence = trigger.data.sequence?.[0] ?? 0
                const selected_ids = new Set()
                for (const source of [...point_sources, ...interval_sources]) {
                    for (const index of source.selected.indices) {
                        const identity = source.data.id[index]
                        if (identity != null)
                            selected_ids.add(identity)
                    }
                }
                const identities = [...selected_ids]
                trigger.patch({
                    sequence: [[0, sequence + 1]],
                    selected_id: [[0, identities.length === 1 ? identities[0] : ""]],
                    selected_count: [[0, identities.length]],
                })
            }
            document.addEventListener("keydown", window[key], true)
        """)
        for source in point_sources:
            source.selected.js_on_change("indices", selected_point_interaction)
        for source in interval_sources:
            source.selected.js_on_change("indices", selected_point_interaction)
        for source in start_sources + end_sources:
            source.selected.js_on_change("indices", selected_point_interaction)
        self.mode.js_on_change("value", selected_point_interaction)
        self.mode.on_change("value", self.mode_changed)
        self.x_range.on_change("start", self.viewport_changed)
        self.x_range.on_change("end", self.viewport_changed)
        self.time_scrollbar.on_change("value", self.time_scrollbar_changed)
        heading = Div(text="<h2 style='margin:0'>Signal Annotation · Local ACQ Viewer and Annotator</h2>"
                      f"<p>{escape(self.recording.name)} · {len(self.recording.channels)} channels · "
                      f"{self.recording.duration:.3f} s · Author: {escape(service.author)}</p>",
                      styles={"background-color": "#123c45", "color": "white", "padding": "18px"})
        help_text = Div(text="<b>Manual workflow</b><p>1. Navigate and zoom into the signal.<br>"
            "2. Register and select an amplitude label, choose Mark amplitude marker, and click the channel, or use numeric times.<br>"
            "3. To move a point, choose Navigate, click its marker once to select it, then drag it with the mouse.<br>"
            "4. Register and select an interval label, choose Mark interval, and drag horizontally across its channel plot. "
            "Alternatively, click its start and then Ctrl+click a later time on the same plot. "
            "To resize one, choose Navigate and drag either full-height vertical edge "
            "horizontally; no preliminary selection is needed. When interval edges are visible, "
            "use the XPan toolbar button or Time navigation control to pan.<br>"
            "5. To change a generic point's type, select it in the plot, choose an amplitude label, and click Assign selected amplitude label.<br>"
            "6. In Navigate, select one point or interval in a plot and press Delete to remove it.<br>"
            "7. Use General recording notes for free-text observations about the whole recording. "
            "Save general notes stores the text immediately; Clear general notes saves an empty note.</p>"
            "<p>Points snap to the nearest original sample. Amplitude and interval label catalogs are independent; "
            "numeric creation uses the last plot you interacted with (the first channel initially), "
            "interval handles use continuous time, and every accepted interval gesture is saved immediately. "
            "No detector is run. The trace may be summarized when zoomed out.</p>",
            name="help_content", styles={"max-height": "70vh", "overflow-y": "auto",
                                          "padding": "8px 16px"})
        self.help_dialog = Dialog(title="Help · Manual workflow", content=help_text,
                                  visible=False, close_action="hide", closable=True,
                                  resizable="none", maximizable=False, minimizable=False,
                                  collapsible=False, pinnable=False, name="help_dialog")
        self.help_button = Button(label="Help", name="help", width=100)
        self.help_button.on_click(self.open_help)
        sidebar = column(self.help_button, self.mode,
                         self.amplitude_label_name, add_amplitude_label, self.amplitude_label_select,
                         self.general_notes, row(save_general_notes, clear_general_notes,
                                                 sizing_mode="stretch_width"),
                         self.interval_label_name, add_interval_label, self.interval_label_select,
                         self.start, self.end,
                         add, Div(text="<hr><b>Synchronized view</b>"), self.view_start, self.view_width,
                         row(go, reset, sizing_mode="stretch_width"),
                         self.assign_marker_label,
                         reload_button, export,
                         width=340, height=1130, sizing_mode="fixed")
        plot_column = column(*self.plots.values(), self.time_scrollbar, sizing_mode="stretch_width")
        footer = Div(text="<b>Research prototype.</b> No clinical validation. Listens only on 127.0.0.1. "
                     "Use one tab per ACQ file. No history or undo. Regions/metadata/relationships are preserved; their graphical editor is pending. AI proposals are a future extension, with no detector installed.",
                     styles={"font-size": "12px", "color": "#53646a"})
        doc.title = "Local ACQ annotator"
        doc.add_root(self.help_dialog)
        doc.add_root(column(heading, self.status, row(plot_column, sidebar, sizing_mode="stretch_width"),
                            footer, sizing_mode="stretch_width"))
        self.end.disabled = True
        self.refresh()

    def open_help(self):
        self.help_dialog.visible = True

    def interval_label_options(self):
        return [("", "Choose a registered label")] + [
            (entry["name"], entry["name"])
            for entry in interval_label_catalog(self.service.workspace.metadata)]

    def amplitude_label_options(self):
        return [("", "Choose a registered label")] + [
            (entry["name"], entry["name"])
            for entry in amplitude_label_catalog(self.service.workspace.metadata)]

    def refresh_amplitude_label_menu(self, selected=None):
        current = self.amplitude_label_select.value if selected is None else selected
        self.amplitude_label_select.options = self.amplitude_label_options()
        values = {value for value, _ in self.amplitude_label_select.options}
        self.amplitude_label_select.value = current if current in values else ""

    def create_amplitude_label(self):
        entry = self.service.add_amplitude_label(self.amplitude_label_name.value)
        self.amplitude_label_name.value = ""
        self.refresh_amplitude_label_menu(entry["name"])
        return entry

    def refresh_interval_label_menu(self, selected=None):
        current = self.interval_label_select.value if selected is None else selected
        self.interval_label_select.options = self.interval_label_options()
        values = {value for value, _ in self.interval_label_select.options}
        self.interval_label_select.value = current if current in values else ""

    def create_interval_label(self):
        entry = self.service.add_interval_label(self.interval_label_name.value)
        self.interval_label_name.value = ""
        self.refresh_interval_label_menu(entry["name"])
        return entry

    def save_general_notes(self):
        return self.service.save_general_notes(self.general_notes.value)

    def clear_general_notes(self):
        self.general_notes.value = ""
        return self.service.save_general_notes("")

    def make_plot(self, ch):
        signal = ColumnDataSource(data={"x": [], "y": []}, name=f"signal_{ch.id}")
        points = ColumnDataSource(data={"id": [], "x": [], "y": [], "label": [], "color": [],
                                        "marker": [], "origin": []}, name=f"points_{ch.id}")
        intervals = ColumnDataSource(data={"id": [], "left": [], "right": [], "bottom": [],
                                           "top": [], "label": [], "color": [], "origin": []},
                                     name=f"intervals_{ch.id}")
        interval_starts = ColumnDataSource(data={"id": [], "x": [], "y": [], "height": [],
                                                 "label": [], "color": [], "origin": []},
                                           name=f"interval_starts_{ch.id}")
        interval_ends = ColumnDataSource(data={"id": [], "x": [], "y": [], "height": [],
                                               "label": [], "color": [], "origin": []},
                                         name=f"interval_ends_{ch.id}")
        p = figure(title=f"{ch.name} · {ch.sample_rate:g} Hz", height=160, x_range=self.x_range,
                   y_range=Range1d(-1, 1), tools="xpan,xwheel_zoom,xbox_zoom,reset,tap",
                   active_drag="xpan", active_scroll="xwheel_zoom", sizing_mode="stretch_width",
                   name=f"plot_{ch.id}", min_width=440)
        self.pan_tools[ch.id] = p.toolbar.active_drag
        box = BoxSelectTool(dimensions="width", renderers=[])
        p.add_tools(box)
        p.line("x", "y", source=signal, color="#204e63", line_width=1.2)
        point_renderer = p.scatter("x", "y", source=points, color="color", marker="marker", size=12,
                                   selection_color="#ffd166", selection_line_color="#111111",
                                   selection_line_width=3, nonselection_alpha=.35)
        tap_tool = p.select_one(TapTool)
        point_draw = PointDrawTool(renderers=[point_renderer], add=False, drag=True,
                                   description="Drag points", visible=True)
        p.add_tools(point_draw)
        area = p.quad(left="left", right="right", bottom="bottom", top="top", source=intervals,
                      fill_color="color", line_color="color", fill_alpha=.16, line_alpha=.45,
                      selection_fill_color="#ffd166", selection_fill_alpha=.42,
                      selection_line_color="#111111", selection_line_alpha=1,
                      selection_line_width=3, nonselection_fill_alpha=.07,
                      nonselection_line_alpha=.2)
        start_renderer = p.rect("x", "y", width=INTERVAL_EDGE_HIT_WIDTH, height="height",
                                source=interval_starts,
                                width_units="screen", height_units="data", fill_color="color",
                                fill_alpha=.12, line_color="color", line_alpha=.9, line_width=2,
                                selection_fill_color="#ffd166", selection_fill_alpha=.3,
                                selection_line_color="#111111", selection_line_width=3)
        end_renderer = p.rect("x", "y", width=INTERVAL_EDGE_HIT_WIDTH, height="height",
                              source=interval_ends,
                              width_units="screen", height_units="data", fill_color="color",
                              fill_alpha=.12, line_color="color", line_alpha=.9, line_width=2,
                              selection_fill_color="#ffd166", selection_fill_alpha=.3,
                              selection_line_color="#111111", selection_line_width=3)
        tap_tool.renderers = [point_renderer, area]
        p.toolbar.active_tap = tap_tool
        edge_draw = PointDrawTool(renderers=[start_renderer, end_renderer], add=False, drag=True,
                                  description="Drag interval edges")
        p.add_tools(edge_draw)
        p.add_tools(HoverTool(renderers=[point_renderer], tooltips=[("Label", "@label"), ("t", "@x{0.000000}"),
                                                                 ("Value", "@y{0.000000}"), ("Origin", "@origin")]))
        p.add_tools(HoverTool(renderers=[area], tooltips=[("Interval", "@label"), ("Start", "@left"), ("End", "@right")]))
        p.add_tools(HoverTool(renderers=[start_renderer], tooltips=[("Interval start", "@label"), ("t", "@x{0.000000}")]))
        p.add_tools(HoverTool(renderers=[end_renderer], tooltips=[("Interval end", "@label"), ("t", "@x{0.000000}")]))
        p.xaxis.axis_label, p.yaxis.axis_label = "Time (s)", ch.units
        p.xgrid.grid_line_alpha = .18
        p.ygrid.grid_line_alpha = .18
        p.toolbar.logo = None
        p.on_event(Tap, lambda event, channel_id=ch.id: self.tap(event, channel_id))
        p.on_event(SelectionGeometry, lambda event, channel_id=ch.id: self.selection(event, channel_id))
        points.on_change("data", lambda attr, old, new, channel_id=ch.id:
                         self.point_data_changed(channel_id))
        interval_starts.on_change("data", lambda attr, old, new, channel_id=ch.id:
                                  self.interval_handle_data_changed(channel_id, "start"))
        interval_ends.on_change("data", lambda attr, old, new, channel_id=ch.id:
                                self.interval_handle_data_changed(channel_id, "end"))
        points.selected.on_change("indices", lambda attr, old, new, channel_id=ch.id:
                                  self.point_selection_changed(channel_id, new))
        intervals.selected.on_change("indices", lambda attr, old, new, channel_id=ch.id:
                                     self.interval_selection_changed(channel_id, new))
        interval_starts.selected.on_change("indices", lambda attr, old, new, channel_id=ch.id:
                                           self.interval_handle_selection_changed(
                                               channel_id, "start", new))
        interval_ends.selected.on_change("indices", lambda attr, old, new, channel_id=ch.id:
                                         self.interval_handle_selection_changed(
                                             channel_id, "end", new))
        self.plots[ch.id] = p
        self.point_tap_tools[ch.id] = tap_tool
        self.interval_renderers[ch.id] = area
        self.point_draw_tools[ch.id] = point_draw
        self.interval_handle_sources[ch.id] = {"start": interval_starts, "end": interval_ends}
        self.interval_edge_draw_tools[ch.id] = edge_draw
        # Keep endpoint aliases for callers that inspect the existing interaction map.
        self.interval_draw_tools[ch.id] = {"start": edge_draw, "end": edge_draw}
        self.sources[ch.id] = (signal, points, intervals, box)

    def report(self, message, error=False):
        self.status.text = escape(message)
        self.status.styles = {"color": "#a12725" if error else "#31565b", "padding": "10px",
                              "background-color": "#fff0ee" if error else "#eef7f4"}

    def run(self, action, restore_on_error=False):
        self.reset_pending_interval()
        try:
            result = action()
            self.refresh()
            self.report(f"JSON saved · {len(self.service.workspace.annotations)} annotations · no history.")
            return result
        except Exception as exc:
            # Keep current document alive; failed repository writes do not change memory.
            if restore_on_error:
                self.refresh()
            self.report(f"Action could not be completed: {exc}", error=True)
            return None

    def mode_changed(self, attr, old, new):
        had_pending = self.pending_interval_start is not None
        self.reset_pending_interval()
        self.clear_annotation_selection()
        self.configure_active_drag()
        self.end.disabled = new != "interval"
        if had_pending:
            self.report("Pending interval start cleared because the action changed.")

    def interval_label_changed(self, attr, old, new):
        if self.pending_interval_start is not None:
            self.reset_pending_interval()
            self.report("Pending interval start cleared because the interval label changed.")

    def reset_pending_interval(self):
        self.pending_interval_start = None

    def configure_active_drag(self):
        for ch_id, p in self.plots.items():
            points = self.sources[ch_id][1]
            selected = points.selected.indices
            has_one_point = (len(selected) == 1 and type(selected[0]) is int and
                             0 <= selected[0] < len(points.data["id"]))
            is_navigate = self.mode.value == "navigate"
            can_move_point = is_navigate and has_one_point
            has_visible_edge = any(
                self.interval_handle_sources[ch_id][endpoint].data["id"]
                for endpoint in ("start", "end"))
            use_box = self.mode.value == "interval"
            self.point_draw_tools[ch_id].visible = is_navigate
            self.interval_edge_draw_tools[ch_id].visible = is_navigate
            active_draw = (self.point_draw_tools[ch_id] if can_move_point else
                           self.interval_edge_draw_tools[ch_id]
                           if is_navigate and has_visible_edge else None)
            p.toolbar.active_tap = (self.point_draw_tools[ch_id] if can_move_point else
                                    self.point_tap_tools[ch_id])
            p.toolbar.active_drag = (active_draw if active_draw else
                                     self.sources[ch_id][3] if use_box else self.pan_tools[ch_id])

    def clear_annotation_selection(self):
        self.syncing_annotation_selection = True
        try:
            for _, points, intervals, _ in self.sources.values():
                points.selected.indices = []
                intervals.selected.indices = []
            for handles in self.interval_handle_sources.values():
                handles["start"].selected.indices = []
                handles["end"].selected.indices = []
        finally:
            self.syncing_annotation_selection = False

    def add_numeric(self):
        if self.mode.value == "navigate":
            raise ValueError("Choose an amplitude marker or interval first.")
        if self.mode.value == "interval":
            return self.service.add_catalog_interval(self.active_channel_id, float(self.start.value),
                                                     float(self.end.value),
                                                     self.interval_label_select.value)
        return self.service.add_catalog_marker(self.active_channel_id, float(self.start.value),
                                               self.amplitude_label_select.value)

    def tap(self, event, channel_id):
        self.active_channel_id = channel_id
        if self.mode.value == "interval":
            self.click_interval(event, channel_id)
            return
        if self.mode.value != "marker" or event.x is None:
            return
        self.start.value = float(event.x)
        self.run(self.add_numeric)

    def click_interval(self, event, channel_id):
        """Create a local interval from an ordinary click and a later Ctrl+click."""
        modifiers = event.modifiers or {}
        ctrl = bool(modifiers.get("ctrl", False))
        unsupported = bool(modifiers.get("shift", False) or modifiers.get("alt", False))
        if unsupported:
            self.reset_pending_interval()
            self.report("Use an ordinary click for the interval start and Ctrl+click for its end.",
                        error=True)
            return

        if not ctrl:
            if self.pending_interval_start is not None:
                self.reset_pending_interval()
                self.report("Pending interval start cleared. Click again to choose a new start.",
                            error=True)
                return
            label = self.interval_label_select.value
            registered = {entry["name"]
                          for entry in interval_label_catalog(self.service.workspace.metadata)}
            if label not in registered:
                self.report("Choose a registered interval label before setting its start.",
                            error=True)
                return
            if event.x is None or not np.isfinite(float(event.x)):
                self.report("Choose a valid interval start.", error=True)
                return
            start = float(event.x)
            if not 0 <= start < self.recording.duration:
                self.report("Choose an interval start inside the recording.", error=True)
                return
            self.pending_interval_start = {
                "start": start, "channel_id": channel_id, "label": label,
            }
            self.start.value = start
            self.report(f"Interval start set at {start:.6f} s. "
                        "Ctrl+click a later time on the same plot.")
            return

        pending = self.pending_interval_start
        self.reset_pending_interval()
        if pending is None:
            self.report("Click an interval start before Ctrl+clicking its end.", error=True)
            return
        if pending["channel_id"] != channel_id:
            self.report("The interval end must be on the same plot as its start.", error=True)
            return
        if pending["label"] != self.interval_label_select.value:
            self.report("The interval label changed; click a new start.", error=True)
            return
        if event.x is None or not np.isfinite(float(event.x)):
            self.report("Choose a valid interval end.", error=True)
            return
        end = float(event.x)
        if end <= pending["start"]:
            self.report("The interval end must be later than its start.", error=True)
            return
        self.start.value, self.end.value = pending["start"], end
        self.run(self.add_numeric)

    def selection(self, event, channel_id):
        if not event.final or event.geometry.get("type") != "rect":
            return
        self.active_channel_id = channel_id
        if self.mode.value != "interval":
            return
        start, end = sorted([float(event.geometry["x0"]), float(event.geometry["x1"])])
        self.start.value, self.end.value = start, end
        self.run(self.add_numeric)

    def point_selection_changed(self, channel_id, indices):
        if self.syncing_annotation_selection:
            return
        source = self.sources[channel_id][1]
        valid = ([i for i in indices if type(i) is int and 0 <= i < len(source.data["id"])]
                 if self.mode.value == "navigate" else [])
        if valid and self.selected_interval_handle() is not None:
            valid = []
        self.syncing_annotation_selection = True
        try:
            source.selected.indices = valid[-1:] if valid else []
            for other_id, (_, other, intervals, _) in self.sources.items():
                if other_id != channel_id:
                    other.selected.indices = []
                if valid:
                    intervals.selected.indices = []
            if valid:
                for handles in self.interval_handle_sources.values():
                    handles["start"].selected.indices = []
                    handles["end"].selected.indices = []
        finally:
            self.syncing_annotation_selection = False
        self.configure_active_drag()
        if valid:
            self.active_channel_id = channel_id
            self.report("Marker selected. Drag it to move it, or press Delete to remove it.")

    def interval_selection_changed(self, channel_id, indices):
        """Keep one interval identity selected across all plots and rendered copies."""
        if self.syncing_annotation_selection:
            return
        source = self.sources[channel_id][2]
        valid = ([i for i in indices if type(i) is int and 0 <= i < len(source.data["id"])]
                 if self.mode.value == "navigate" else [])
        if valid and self.selected_interval_handle() is not None:
            valid = []
        identity = source.data["id"][valid[-1]] if valid else None
        self.syncing_annotation_selection = True
        try:
            source.selected.indices = valid[-1:] if valid else []
            if identity is not None:
                for _, points, intervals, _ in self.sources.values():
                    points.selected.indices = []
                    if intervals is not source:
                        intervals.selected.indices = []
                for handles in self.interval_handle_sources.values():
                    handles["start"].selected.indices = []
                    handles["end"].selected.indices = []
        finally:
            self.syncing_annotation_selection = False
        self.configure_active_drag()
        if identity is not None:
            self.active_channel_id = channel_id
            self.report("Interval selected. Press Delete to remove it, or drag either vertical edge to resize it.")

    def selected_interval_handle(self):
        """Return one valid selected endpoint, if present."""
        for channel_id, handles in self.interval_handle_sources.items():
            for endpoint, source in handles.items():
                indices = source.selected.indices
                if (len(indices) == 1 and type(indices[0]) is int and
                        0 <= indices[0] < len(source.data["id"])):
                    return channel_id, endpoint, indices[0]
        return None

    def interval_handle_selection_changed(self, channel_id, endpoint, indices):
        """Make one selected interval endpoint the active Tap+Drag edit gesture."""
        if self.syncing_annotation_selection:
            return
        source = self.interval_handle_sources[channel_id][endpoint]
        valid = ([i for i in indices if type(i) is int and 0 <= i < len(source.data["id"])]
                 if self.mode.value == "navigate" else [])
        self.syncing_annotation_selection = True
        try:
            for _, points, intervals, _ in self.sources.values():
                points.selected.indices = []
                intervals.selected.indices = []
            for other_id, handles in self.interval_handle_sources.items():
                for other_endpoint, other_source in handles.items():
                    other_source.selected.indices = (valid[-1:] if valid and
                        other_id == channel_id and other_endpoint == endpoint else [])
        finally:
            self.syncing_annotation_selection = False
        self.configure_active_drag()
        if valid:
            self.active_channel_id = channel_id
            self.report(f"Interval {endpoint} selected. Drag it to resize the interval.")

    def delete_selected_annotation_event(self, attr, old, new):
        count = new.get("selected_count", [0])
        identity = new.get("selected_id", [""])
        selected_ids = identity if count == [1] and len(identity) == 1 and identity[0] else []
        self.delete_selected_annotation(selected_ids)

    def delete_selected_annotation(self, selected_ids=None):
        ids = list(selected_ids) if selected_ids is not None else []
        if selected_ids is None:
            for _, points, intervals, _ in self.sources.values():
                ids.extend(points.data["id"][i] for i in points.selected.indices
                           if type(i) is int and 0 <= i < len(points.data["id"]))
                ids.extend(intervals.data["id"][i] for i in intervals.selected.indices
                           if type(i) is int and 0 <= i < len(intervals.data["id"]))
        ids = list(dict.fromkeys(ids))
        annotation = (next((item for item in self.service.workspace.annotations
                            if len(ids) == 1 and item.id == ids[0]), None)
                      if ids else None)
        if (len(ids) != 1 or annotation is None or
                annotation.location.type not in {"point", "interval"}):
            self.report("Select exactly one point or interval in the plot before deleting it.",
                        error=True)
            return
        self.run(lambda: self.service.delete(ids))

    def selected_plot_point(self):
        """Return the one valid point selected directly in a plot."""
        ids = []
        for _, points, _, _ in self.sources.values():
            ids.extend(points.data["id"][i] for i in points.selected.indices
                       if type(i) is int and 0 <= i < len(points.data["id"]))
        if len(ids) != 1:
            raise ValueError("Select exactly one point in the plot.")
        identity = ids[0]
        annotation = next((a for a in self.service.workspace.annotations if a.id == identity), None)
        if annotation is None or annotation.location.type != "point":
            raise ValueError("The selected point is not a current annotation.")
        return annotation

    def reassign_selected_point_label(self):
        annotation = self.selected_plot_point()
        return self.service.reassign_point_label(annotation.id, self.amplitude_label_select.value)

    def point_data_changed(self, channel_id):
        """Validate a completed PointDraw mutation and commit its final x coordinate."""
        if self.refreshing_points:
            return
        if self.mode.value != "navigate":
            self.refresh()
            self.report("Choose Navigate before moving an amplitude marker.", error=True)
            return
        try:
            source = self.sources[channel_id][1]
            ids, xs, ys = (source.data[key] for key in ("id", "x", "y"))
            if not len(ids) == len(xs) == len(ys):
                raise ValueError("Invalid point renderer data.")
            by_id = {annotation.id: annotation for annotation in self.service.workspace.annotations}
            changed = []
            for identity, raw_x, raw_y in zip(ids, xs, ys):
                annotation = by_id.get(identity)
                if (annotation is None or annotation.location.type != "point" or
                        annotation.channel_id != channel_id):
                    raise ValueError("The dragged point is not a current annotation.")
                x, y = float(raw_x), float(raw_y)
                if x != annotation.start or y != annotation.value:
                    changed.append((identity, x))
            if not changed:
                return
            if len(changed) != 1:
                raise ValueError("Move exactly one point at a time.")
        except Exception as exc:
            self.refresh()
            self.report(f"Action could not be completed: {exc}", error=True)
            return
        identity, x = changed[0]
        self.run(lambda: self.service.move_point(identity, x), restore_on_error=True)

    def interval_handle_data_changed(self, channel_id, endpoint):
        """Commit one completed endpoint drag; vertical movement is display-only."""
        if self.refreshing_interval_handles:
            return
        if self.mode.value != "navigate":
            self.refresh()
            self.report("Choose Navigate before resizing an interval.", error=True)
            return
        try:
            source = self.interval_handle_sources[channel_id][endpoint]
            ids, xs, ys = (source.data[key] for key in ("id", "x", "y"))
            if not len(ids) == len(xs) == len(ys):
                raise ValueError("Invalid interval handle renderer data.")
            by_id = {annotation.id: annotation for annotation in self.service.workspace.annotations}
            expected_ids = [annotation.id for annotation in self.service.workspace.annotations
                if annotation.location.type == "interval"
                and (not annotation.location.channel_ids or channel_id in annotation.location.channel_ids)
                and not (annotation.end < self.x_range.start or annotation.start > self.x_range.end)]
            if list(ids) != expected_ids:
                raise ValueError("The dragged interval is not a current annotation on this channel.")
            changed = []
            for identity, raw_x in zip(ids, xs):
                annotation = by_id.get(identity)
                if annotation is None or annotation.location.type != "interval":
                    raise ValueError("The dragged interval is not a current annotation.")
                if isinstance(raw_x, (bool, np.bool_)):
                    raise ValueError("The dragged interval time is invalid.")
                x = float(raw_x)
                if x != getattr(annotation, endpoint):
                    changed.append((identity, x))
            if not changed:
                # PointDraw also changes y.  Handles are always redrawn at the
                # current plot midpoint and vertical movement is never persisted.
                # The shared edge tool emits both sources on drop, so leave the
                # untouched opposite endpoint alone.
                handle_y = (self.plots[channel_id].y_range.start +
                            self.plots[channel_id].y_range.end) / 2
                if any(not np.isfinite(float(raw_y)) or float(raw_y) != handle_y
                       for raw_y in ys):
                    self.refresh()
                return
            if len(changed) != 1:
                raise ValueError("Resize exactly one interval endpoint at a time.")
        except Exception as exc:
            self.refresh()
            self.report(f"Action could not be completed: {exc}", error=True)
            return
        identity, seconds = changed[0]
        self.run(lambda: self.service.resize_interval(identity, endpoint, seconds),
                 restore_on_error=True)

    def go_window(self):
        start, width = float(self.view_start.value), float(self.view_width.value)
        if not np.isfinite(start) or not np.isfinite(width) or width <= 0 or not 0 <= start < self.recording.duration:
            raise ValueError("Invalid window.")
        self.set_visible_window(start, min(self.recording.duration, start + width))

    def full_view(self):
        self.set_visible_window(0, self.recording.duration)

    def bounded_visible_window(self, start, end):
        """Return a finite visible interval constrained to the recording bounds."""
        duration = self.recording.duration
        start, end = float(start), float(end)
        if not np.isfinite(start) or not np.isfinite(end):
            return self.x_range.start, self.x_range.end
        width = end - start
        if width >= duration:
            return 0, duration
        if width <= 0:
            width = min(.001, duration)
        start = min(max(start, 0), duration - width)
        return start, start + width

    def set_visible_window(self, start, end):
        """Update the shared range and every viewport control without callback recursion."""
        start, end = self.bounded_visible_window(start, end)
        self.syncing_viewport = True
        try:
            self.x_range.start, self.x_range.end = start, end
            self.view_start.value = start
            self.view_width.value = end - start
            self.time_scrollbar.value = (start, end)
        finally:
            self.syncing_viewport = False
        self.schedule_refresh(None, None, None)

    def viewport_changed(self, attr, old, new):
        if not self.syncing_viewport:
            self.set_visible_window(self.x_range.start, self.x_range.end)

    def time_scrollbar_changed(self, attr, old, new):
        if not self.syncing_viewport:
            self.set_visible_window(*new)

    def schedule_refresh(self, attr, old, new):
        if self.pending_refresh is not None:
            self.doc.remove_timeout_callback(self.pending_refresh)
        self.pending_refresh = self.doc.add_timeout_callback(self.refresh, 80)

    def refresh(self):
        self.pending_refresh = None
        self.refresh_amplitude_label_menu()
        self.refresh_interval_label_menu()
        self.general_notes.value = general_notes(self.service.workspace.metadata)
        self.refreshing_points = True
        self.refreshing_interval_handles = True
        try:
            for ch in self.recording.channels:
                signal, points, intervals, _ = self.sources[ch.id]
                x, y = visible_samples(ch, self.x_range.start, self.x_range.end)
                signal.data = {"x": x, "y": y}
                finite = y[np.isfinite(y)]
                if len(finite):
                    lo, hi = float(np.min(finite)), float(np.max(finite))
                    margin = max((hi - lo) * .12, abs(hi) * .002, 1e-6)
                    self.plots[ch.id].y_range.start, self.plots[ch.id].y_range.end = lo - margin, hi + margin
                colors = point_colors(self.service.workspace.annotations)
                p_data = {k: [] for k in ["id", "x", "y", "label", "color", "marker", "origin"]}
                i_data = {k: [] for k in ["id", "left", "right", "bottom", "top", "label",
                                                  "color", "origin"]}
                start_data = {k: [] for k in ["id", "x", "y", "height", "label", "color", "origin"]}
                end_data = {k: [] for k in ["id", "x", "y", "height", "label", "color", "origin"]}
                handle_y = (self.plots[ch.id].y_range.start + self.plots[ch.id].y_range.end) / 2
                handle_height = self.plots[ch.id].y_range.end - self.plots[ch.id].y_range.start
                for a in self.service.workspace.annotations:
                    if a.location.type == "interval" and (not a.location.channel_ids or ch.id in a.location.channel_ids):
                        if a.end < self.x_range.start or a.start > self.x_range.end:
                            continue
                        i_data["id"].append(a.id)
                        i_data["left"].append(a.start)
                        i_data["right"].append(a.end)
                        i_data["bottom"].append(self.plots[ch.id].y_range.start)
                        i_data["top"].append(self.plots[ch.id].y_range.end)
                        i_data["label"].append(a.label.text)
                        i_data["origin"].append(a.origin)
                        color = interval_color(a, self.service.workspace)
                        i_data["color"].append(color)
                        for handle_data, seconds in ((start_data, a.start), (end_data, a.end)):
                            handle_data["id"].append(a.id)
                            handle_data["x"].append(seconds)
                            handle_data["y"].append(handle_y)
                            handle_data["height"].append(handle_height)
                            handle_data["label"].append(a.label.text)
                            handle_data["color"].append(color)
                            handle_data["origin"].append(a.origin)
                    elif a.channel_id == ch.id and a.location.type == "point":
                        p_data["id"].append(a.id)
                        p_data["x"].append(a.start)
                        p_data["y"].append(a.value)
                        p_data["label"].append(a.label.text)
                        p_data["color"].append(colors[a.id])
                        p_data["marker"].append("triangle" if a.kind == "maximum" else "inverted_triangle" if a.kind == "minimum" else "circle")
                        p_data["origin"].append(a.origin)
                points.data, intervals.data = p_data, i_data
                self.interval_handle_sources[ch.id]["start"].data = start_data
                self.interval_handle_sources[ch.id]["end"].data = end_data
                # A refreshed index may refer to a different annotation; require a new plot selection.
                points.selected.indices = []
                intervals.selected.indices = []
                self.interval_handle_sources[ch.id]["start"].selected.indices = []
                self.interval_handle_sources[ch.id]["end"].selected.indices = []
        finally:
            self.refreshing_points = False
            self.refreshing_interval_handles = False
        self.configure_active_drag()

    def export(self):
        try:
            path = self.sidecar.with_suffix(".csv")
            export_annotations_csv(self.service.workspace, self.recording, path)
            self.report(f"Current CSV exported to: {path}. That CSV is replaced on the next export.")
        except Exception as exc:
            self.report(f"Could not export: {exc}", error=True)


def build_document(doc, service: AnnotationService, sidecar: Path) -> Viewer:
    return Viewer(doc, service, sidecar)
