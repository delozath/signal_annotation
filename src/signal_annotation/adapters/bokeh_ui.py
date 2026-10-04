"""Driving adapter: browser UI. All durable changes go through use cases."""
from html import escape
from pathlib import Path
import numpy as np
from bokeh.events import SelectionGeometry, Tap
from bokeh.layouts import column, row
from bokeh.models import (BoxSelectTool, Button, ColumnDataSource, DataTable, Div,
                          HoverTool, PanTool, Range1d, Select, Spinner, TableColumn, TextInput)
from bokeh.plotting import figure
from ..application import AnnotationService
from .display import visible_samples
from .storage import export_annotations_csv

COLORS = {"maximum": "#d9553b", "minimum": "#2b70bd", "interval": "#2d927d"}


class Viewer:
    def __init__(self, doc, service: AnnotationService, sidecar: Path):
        self.doc, self.service, self.sidecar = doc, service, Path(sidecar)
        self.recording = service.recording
        self.pending_refresh = None
        self.plots, self.sources = {}, {}
        self.x_range = Range1d(0, min(8, self.recording.duration), bounds=(0, self.recording.duration))
        self.status = Div(text="Ready. Every change is saved automatically on this computer.", name="status",
                          styles={"color": "#31565b", "padding": "10px", "background-color": "#eef7f4"})
        self.mode = Select(title="Action", value="navigate", name="mode", options=[
            ("navigate", "Navigate"), ("maximum", "Mark manual maximum"),
            ("minimum", "Mark manual minimum"), ("interval", "Mark interval")])
        self.channel_select = Select(title="Channel (form / global interval)", value=self.recording.channels[0].id,
            options=[(c.id, c.name) for c in self.recording.channels] + [("__all__", "All: global interval")], name="channel")
        self.label = TextInput(title="Label", value="event", max_length=200, name="label")
        self.start = Spinner(title="Time / start (s)", value=0, low=0, high=self.recording.duration,
                             step=.001, format="0.000000", name="start")
        self.end = Spinner(title="Interval end (s)", value=min(1, self.recording.duration), low=0,
                           high=self.recording.duration, step=.001, format="0.000000", name="end")
        add = Button(label="Add with these times", button_type="primary", name="add")
        add.on_click(lambda: self.run(self.add_numeric))
        self.view_start = Spinner(title="View start (s)", value=0, low=0, high=self.recording.duration, step=1, name="view_start")
        self.view_width = Spinner(title="Window (s)", value=min(8, self.recording.duration), low=.01,
                                  high=self.recording.duration, step=1, name="view_width")
        go = Button(label="Go to window", name="go")
        go.on_click(lambda: self.run(self.go_window))
        reset = Button(label="View full recording", name="full")
        reset.on_click(self.full_view)
        load_edit = Button(label="Load selected row into form", name="load_edit")
        load_edit.on_click(self.load_selected)
        edit = Button(label="Save selected row edit", name="edit")
        edit.on_click(lambda: self.run(self.edit_selected))
        reload_button = Button(label="Reload saved state", name="reload")
        reload_button.on_click(lambda: self.run(self.service.reload))
        export = Button(label="Export annotation CSV", button_type="success", name="export")
        export.on_click(self.export)
        delete = Button(label="Delete selected rows", button_type="warning", name="delete")
        delete.on_click(lambda: self.run(self.delete_selected))
        self.table_source = ColumnDataSource(data=self.empty_table(), name="annotations_source")
        table = DataTable(source=self.table_source, columns=[
            TableColumn(field="channel", title="Channel"), TableColumn(field="kind", title="Type"),
            TableColumn(field="start", title="Start (s)"), TableColumn(field="end", title="End (s)"),
            TableColumn(field="label", title="Label"), TableColumn(field="origin", title="Origin"),
            TableColumn(field="author", title="Author")], height=220, sizing_mode="stretch_width",
            editable=False, selectable="checkbox", index_position=None, name="annotations_table")
        for ch in self.recording.channels:
            self.make_plot(ch)
        self.mode.on_change("value", self.mode_changed)
        self.x_range.on_change("start", self.schedule_refresh)
        self.x_range.on_change("end", self.schedule_refresh)
        heading = Div(text="<h2 style='margin:0'>Signal Annotation · Local ACQ Viewer and Annotator</h2>"
                      f"<p>{escape(self.recording.name)} · {len(self.recording.channels)} channels · "
                      f"{self.recording.duration:.3f} s · Author: {escape(service.author)}</p>",
                      styles={"background-color": "#123c45", "color": "white", "padding": "18px"})
        help_text = Div(text="<b>Manual workflow</b><p>1. Navigate and zoom into the signal.<br>"
            "2. Choose maximum or minimum and click the channel, or use numeric times.<br>"
            "3. For intervals, drag a horizontal box.</p>"
            "<p>Points snap to the nearest original sample. The maximum/minimum label is your decision; "
            "no detector is run. The trace may be summarized when zoomed out.</p>")
        sidebar = column(help_text, self.mode, self.channel_select, self.label, self.start, self.end,
                         add, Div(text="<hr><b>Synchronized view</b>"), self.view_start, self.view_width,
                         row(go, reset, sizing_mode="stretch_width"), load_edit, edit, reload_button, export,
                         width=340, height=1130, sizing_mode="fixed")
        plot_column = column(*self.plots.values(), sizing_mode="stretch_width")
        footer = Div(text="<b>Research prototype.</b> No clinical validation. Listens only on 127.0.0.1. "
                     "Use one tab per ACQ file. No history or undo. Regions/metadata/relationships are preserved; their graphical editor is pending. AI proposals are a future extension, with no detector installed.",
                     styles={"font-size": "12px", "color": "#53646a"})
        doc.title = "Local ACQ annotator"
        doc.add_root(column(heading, self.status, row(plot_column, sidebar, sizing_mode="stretch_width"),
                            Div(text="<h3>Saved annotations</h3>"), table, delete, footer,
                            sizing_mode="stretch_width"))
        self.end.disabled = True
        self.refresh()

    @staticmethod
    def empty_table():
        return {k: [] for k in ["id", "channel", "kind", "start", "end", "label", "origin", "author"]}

    def make_plot(self, ch):
        signal = ColumnDataSource(data={"x": [], "y": []}, name=f"signal_{ch.id}")
        points = ColumnDataSource(data={"x": [], "y": [], "label": [], "color": [], "marker": [], "origin": []})
        intervals = ColumnDataSource(data={"left": [], "right": [], "bottom": [], "top": [], "label": []})
        p = figure(title=f"{ch.name} · {ch.sample_rate:g} Hz", height=160, x_range=self.x_range,
                   y_range=Range1d(-1, 1), tools="xpan,xwheel_zoom,xbox_zoom,reset,tap",
                   active_drag="xpan", active_scroll="xwheel_zoom", sizing_mode="stretch_width",
                   name=f"plot_{ch.id}", min_width=440)
        box = BoxSelectTool(dimensions="width", renderers=[])
        p.add_tools(box)
        p.line("x", "y", source=signal, color="#204e63", line_width=1.2)
        point_renderer = p.scatter("x", "y", source=points, color="color", marker="marker", size=10)
        area = p.quad(left="left", right="right", bottom="bottom", top="top", source=intervals,
                      fill_color=COLORS["interval"], fill_alpha=.16, line_alpha=.45)
        p.add_tools(HoverTool(renderers=[point_renderer], tooltips=[("Label", "@label"), ("t", "@x{0.000000}"),
                                                                 ("Value", "@y{0.000000}"), ("Origin", "@origin")]))
        p.add_tools(HoverTool(renderers=[area], tooltips=[("Interval", "@label"), ("Start", "@left"), ("End", "@right")]))
        p.xaxis.axis_label, p.yaxis.axis_label = "Time (s)", ch.units
        p.xgrid.grid_line_alpha = .18
        p.ygrid.grid_line_alpha = .18
        p.toolbar.logo = None
        p.on_event(Tap, lambda event, channel_id=ch.id: self.tap(event, channel_id))
        p.on_event(SelectionGeometry, lambda event, channel_id=ch.id: self.selection(event, channel_id))
        self.plots[ch.id] = p
        self.sources[ch.id] = (signal, points, intervals, box)

    def report(self, message, error=False):
        self.status.text = escape(message)
        self.status.styles = {"color": "#a12725" if error else "#31565b", "padding": "10px",
                              "background-color": "#fff0ee" if error else "#eef7f4"}

    def run(self, action):
        try:
            result = action()
            self.refresh()
            self.report(f"JSON saved · {len(self.service.workspace.annotations)} annotations · no history.")
            return result
        except Exception as exc:
            # Keep current document alive; failed repository writes do not change memory.
            self.report(f"Action could not be completed: {exc}", error=True)
            return None

    def mode_changed(self, attr, old, new):
        for ch_id, p in self.plots.items():
            p.toolbar.active_drag = self.sources[ch_id][3] if new == "interval" else p.select_one(PanTool)
        self.end.disabled = new != "interval"
        if self.label.value in {"event", "maximum", "minimum", "interval"}:
            self.label.value = {"maximum": "maximum", "minimum": "minimum", "interval": "interval"}.get(new, "event")

    def add_numeric(self):
        if self.mode.value == "navigate":
            raise ValueError("Choose maximum, minimum, or interval first.")
        channel_id = None if self.channel_select.value == "__all__" else self.channel_select.value
        return self.service.add(self.mode.value, channel_id, float(self.start.value),
                                float(self.end.value) if self.mode.value == "interval" else None, self.label.value)

    def tap(self, event, channel_id):
        if self.mode.value not in {"maximum", "minimum"} or event.x is None:
            return
        self.channel_select.value = channel_id
        self.start.value = float(event.x)
        self.run(self.add_numeric)

    def selection(self, event, channel_id):
        if self.mode.value != "interval" or not event.final or event.geometry.get("type") != "rect":
            return
        start, end = sorted([float(event.geometry["x0"]), float(event.geometry["x1"])])
        if self.channel_select.value != "__all__":
            self.channel_select.value = channel_id
        self.start.value, self.end.value = start, end
        self.run(self.add_numeric)

    def delete_selected(self):
        ids = [self.table_source.data["id"][i] for i in self.table_source.selected.indices]
        self.service.delete(ids)

    def go_window(self):
        start, width = float(self.view_start.value), float(self.view_width.value)
        if not np.isfinite(start) or not np.isfinite(width) or width <= 0 or not 0 <= start < self.recording.duration:
            raise ValueError("Invalid window.")
        self.x_range.start, self.x_range.end = start, min(self.recording.duration, start + width)

    def full_view(self):
        self.x_range.start, self.x_range.end = 0, self.recording.duration

    def schedule_refresh(self, attr, old, new):
        if self.pending_refresh is not None:
            self.doc.remove_timeout_callback(self.pending_refresh)
        self.pending_refresh = self.doc.add_timeout_callback(self.refresh, 80)

    def refresh(self):
        self.pending_refresh = None
        for ch in self.recording.channels:
            signal, points, intervals, _ = self.sources[ch.id]
            x, y = visible_samples(ch, self.x_range.start, self.x_range.end)
            signal.data = {"x": x, "y": y}
            finite = y[np.isfinite(y)]
            if len(finite):
                lo, hi = float(np.min(finite)), float(np.max(finite))
                margin = max((hi - lo) * .12, abs(hi) * .002, 1e-6)
                self.plots[ch.id].y_range.start, self.plots[ch.id].y_range.end = lo - margin, hi + margin
            p_data = {k: [] for k in ["x", "y", "label", "color", "marker", "origin"]}
            i_data = {k: [] for k in ["left", "right", "bottom", "top", "label"]}
            for a in self.service.workspace.annotations:
                if a.location.type == "interval" and (not a.location.channel_ids or ch.id in a.location.channel_ids):
                    if a.end < self.x_range.start or a.start > self.x_range.end:
                        continue
                    i_data["left"].append(a.start)
                    i_data["right"].append(a.end)
                    i_data["bottom"].append(self.plots[ch.id].y_range.start)
                    i_data["top"].append(self.plots[ch.id].y_range.end)
                    i_data["label"].append(a.label.text)
                elif a.channel_id == ch.id and a.location.type == "point":
                    p_data["x"].append(a.start)
                    p_data["y"].append(a.value)
                    p_data["label"].append(a.label.text)
                    p_data["color"].append(COLORS.get(a.kind, "#7758a6"))
                    p_data["marker"].append("triangle" if a.kind == "maximum" else "inverted_triangle" if a.kind == "minimum" else "circle")
                    p_data["origin"].append(a.origin)
            points.data, intervals.data = p_data, i_data
        data = self.empty_table()
        for a in self.service.workspace.annotations:
            for key, value in {"id": a.id, "channel": ", ".join(self.recording.channel(cid).name for cid in a.location.channel_ids) or "Whole recording",
                               "kind": f"{a.location.type}: {a.label.code}", "start": "" if a.start is None else round(a.start, 6), "end": "" if a.end is None else round(a.end, 6),
                               "label": a.label.text, "origin": a.origin, "author": a.author}.items():
                data[key].append(value)
        self.table_source.selected.indices = []
        self.table_source.data = data

    def selected_annotation(self):
        indices = self.table_source.selected.indices
        if len(indices) != 1:
            raise ValueError("Select exactly one row.")
        identity = self.table_source.data["id"][indices[0]]
        return next(a for a in self.service.workspace.annotations if a.id == identity)

    def load_selected(self):
        try:
            a = self.selected_annotation()
            if a.origin != "manual" or a.kind not in {"maximum", "minimum", "interval"} or len(a.location.channel_ids) > 1:
                raise ValueError("The graphical editor supports only manual maxima/minima and one-channel or global intervals.")
            self.mode.value = a.kind
            self.channel_select.value = a.channel_id or "__all__"
            self.start.value = a.start
            if a.end is not None:
                self.end.value = a.end
            self.label.value = a.label.text
            self.report("Form loaded. Adjust the fields and click Save edit; there is no history or undo.")
        except Exception as exc:
            self.report(str(exc), error=True)

    def edit_selected(self):
        a = self.selected_annotation()
        return self.service.edit(a.id, self.mode.value,
            None if self.channel_select.value == "__all__" else self.channel_select.value,
            float(self.start.value), float(self.end.value) if self.mode.value == "interval" else None, self.label.value)

    def export(self):
        try:
            path = self.sidecar.with_suffix(".csv")
            export_annotations_csv(self.service.workspace, self.recording, path)
            self.report(f"Current CSV exported to: {path}. That CSV is replaced on the next export.")
        except Exception as exc:
            self.report(f"Could not export: {exc}", error=True)


def build_document(doc, service: AnnotationService, sidecar: Path) -> Viewer:
    return Viewer(doc, service, sidecar)
