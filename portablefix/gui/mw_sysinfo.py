"""System-info panel, polling, speed test and diagnostics export."""

from PySide6.QtCore import QTimer, QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from pathlib import Path

from .. import diagnostics, sysinfo
from ..version import APP_VERSION


class SysinfoMixin:
    """System-info panel, polling, speed test and diagnostics export.

    Methods moved verbatim out of MainWindow, which inherits them."""

    def _build_sysinfo_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("actionCard")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(14, 10, 14, 10)
        panel_layout.setSpacing(4)
        header = QHBoxLayout()
        header.setSpacing(6)
        title = QLabel(self._t("sysinfo_title"))
        title.setObjectName("cardHeading")
        header.addWidget(title)
        header.addStretch(1)
        self.sysinfo_toggle_button = QToolButton()
        self.sysinfo_toggle_button.setObjectName("actionDetailToggle")
        self.sysinfo_toggle_button.setCheckable(True)
        self.sysinfo_toggle_button.setCursor(Qt.CursorShape.PointingHandCursor)
        header.addWidget(self.sysinfo_toggle_button)
        panel_layout.addLayout(header)
        # The rows scroll inside the panel so its minimum width is the 280 px
        # set here, not the longest GPU name or button caption - the old
        # 460 px floor was a third of the 1501 px minimum window width.
        body_scroll = QScrollArea()
        body_scroll.setWidgetResizable(True)
        body_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        body_scroll.setWidget(body)
        panel_layout.addWidget(body_scroll, 1)

        def apply_collapsed(collapsed: bool) -> None:
            self.settings.sysinfo_collapsed = collapsed
            body_scroll.setVisible(not collapsed)
            title.setVisible(not collapsed)
            panel.setMinimumWidth(0 if collapsed else 280)
            panel.setMaximumWidth(60 if collapsed else 640)
            self.sysinfo_toggle_button.setText("»" if collapsed else "«")
            tip = self._t("sysinfo_expand") if collapsed else self._t("sysinfo_collapse")
            self.sysinfo_toggle_button.setToolTip(tip)
            self.sysinfo_toggle_button.setAccessibleName(tip)

        self.sysinfo_toggle_button.toggled.connect(apply_collapsed)
        self.sysinfo_toggle_button.setChecked(bool(self.settings.sysinfo_collapsed))
        apply_collapsed(bool(self.settings.sysinfo_collapsed))

        self._sysinfo_labels: dict[str, QLabel] = {}

        self.quiet_mode_checkbox = QCheckBox(self._t("quiet_mode_toggle"))
        self.quiet_mode_checkbox.setToolTip(self._t("quiet_mode_tooltip"))
        self.quiet_mode_checkbox.setChecked(self.settings.quiet_mode)
        self.quiet_mode_checkbox.toggled.connect(self._on_quiet_mode_toggled)
        layout.addWidget(self.quiet_mode_checkbox)

        def add_row(key: str, label_key: str, tooltip: str | None = None, long: bool = False) -> None:
            caption = QLabel(self._t(label_key))
            caption.setObjectName("selectionScope")
            value = QLabel(self._t("sysinfo_loading"))
            # These labels show text sourced from hardware/OS reports (GPU
            # name, VPN adapter name, disk health string, ...) - an
            # unprivileged local process can name a device or VPN profile
            # almost anything, so force plain text rather than letting Qt's
            # auto-detection ever render it as rich text.
            value.setTextFormat(Qt.TextFormat.PlainText)
            if tooltip:
                caption.setToolTip(tooltip)
                value.setToolTip(tooltip)
            self._sysinfo_labels[key] = value
            # "long" fields (full CPU/GPU model names, disk health list,
            # VPN status) must stay on one line rather than wrap - the
            # panel is wide enough for that now (400-600px), unlike the
            # fixed 345px it used to be capped at.
            value.setWordWrap(not long)
            row = QHBoxLayout()
            row.setSpacing(6)
            row.addWidget(caption)
            row.addStretch(1)
            row.addWidget(value)
            layout.addLayout(row)

        add_row("os", "sysinfo_os", long=True)
        add_row("uptime", "sysinfo_uptime")
        add_row("cpu_name", "sysinfo_cpu", long=True)
        add_row("cpu_load", "sysinfo_cpu_load")
        add_row("cpu_clock", "sysinfo_cpu_clock", tooltip=self._t("sysinfo_cpu_clock_hint"))
        add_row("ram", "sysinfo_ram")
        add_row("ram_speed", "sysinfo_ram_speed")
        add_row("battery", "sysinfo_battery")
        add_row("disk_health", "sysinfo_disk_health", long=True)
        add_row("gpu_name", "sysinfo_gpu", long=True)
        add_row("gpu_load", "sysinfo_gpu_load")
        add_row("gpu_temp", "sysinfo_gpu_temp")
        add_row("gpu_clock", "sysinfo_gpu_clock")
        add_row("gpu_vram", "sysinfo_gpu_vram")
        add_row("ip", "sysinfo_ip")
        add_row("ping", "sysinfo_ping")
        add_row("vpn", "sysinfo_vpn", long=True)

        # Shown only in quiet mode: the explicit replacements for what the
        # background timers and the start-up update check would do.
        self.quiet_mode_hint_label = QLabel(self._t("quiet_mode_manual_hint"))
        self.quiet_mode_hint_label.setObjectName("selectionScope")
        self.quiet_mode_hint_label.setWordWrap(True)
        layout.addWidget(self.quiet_mode_hint_label)
        self.check_network_button = self._make_selection_button(
            self._t("quiet_mode_check_network_button"), self._on_check_network_clicked
        )
        self.check_network_button.setObjectName("panelBtn")
        self.check_network_button.setToolTip(self._t("quiet_mode_manual_tooltip"))
        layout.addWidget(self.check_network_button)
        self.check_updates_button = self._make_selection_button(
            self._t("quiet_mode_check_updates_button"), self._on_check_updates_clicked
        )
        self.check_updates_button.setObjectName("panelBtn")
        self.check_updates_button.setToolTip(self._t("quiet_mode_manual_tooltip"))
        layout.addWidget(self.check_updates_button)

        self.speed_test_button = self._make_selection_button(
            self._t("sysinfo_speed_test_button"), self._on_speed_test_clicked
        )
        self.speed_test_button.setObjectName("panelBtn")
        self.speed_test_button.setToolTip(self._t("quiet_mode_manual_tooltip"))
        layout.addWidget(self.speed_test_button)
        self.speed_test_result_label = QLabel("")
        self.speed_test_result_label.setWordWrap(True)
        layout.addWidget(self.speed_test_result_label)

        export_diag_button = self._make_selection_button(
            self._t("export_diagnostics_button"), self._on_export_diagnostics_clicked
        )
        export_diag_button.setObjectName("panelBtn")
        layout.addWidget(export_diag_button)

        report_bug_button = self._make_selection_button(
            self._t("report_bug_button"), self._on_report_bug_clicked
        )
        report_bug_button.setObjectName("panelBtn")
        layout.addWidget(report_bug_button)
        layout.addStretch(1)
        self._apply_quiet_mode_ui()
        return panel

    def _apply_quiet_mode_ui(self) -> None:
        quiet = self.settings.quiet_mode
        self.quiet_mode_hint_label.setVisible(quiet)
        self.check_network_button.setVisible(quiet)
        self.check_updates_button.setVisible(quiet)
        for key in ("ping", "vpn"):
            label = self._sysinfo_labels[key]
            if quiet:
                label.setText(self._t("quiet_mode_value"))
            elif label.text() == self._t("quiet_mode_value"):
                # The next tick fills it in; until then don't claim "off".
                label.setText(self._t("sysinfo_loading"))
        if self._quiet_status_label is None:
            self._quiet_status_label = QLabel()
            self._quiet_status_label.setObjectName("selectionScope")
            self.statusBar().addPermanentWidget(self._quiet_status_label)
        self._quiet_status_label.setText(self._t("quiet_mode_status_on" if quiet else "quiet_mode_status_off"))
        # Its own text: the panel tooltip points at "the buttons below".
        self._quiet_status_label.setToolTip(self._t("quiet_mode_status_tooltip"))

    def _on_quiet_mode_toggled(self, checked: bool) -> None:
        if checked == self.settings.quiet_mode:
            return
        self.settings.quiet_mode = checked
        self._persist_settings()
        self._apply_quiet_mode_ui()
        self._apply_polling_state()
        if self._winget_on_quiet_mode_changed is not None:
            self._winget_on_quiet_mode_changed()
        if not checked and self._startup_update_check_skipped:
            self._start_update_check()

    def _on_check_network_clicked(self) -> None:
        # One ping and one VPN check on an explicit click; the busy flags
        # keep a double click from stacking runners.
        self._on_ping_tick()
        self._on_vpn_tick()

    def _on_check_updates_clicked(self) -> None:
        self._start_update_check(manual=True)

    def _apply_polling_state(self) -> None:
        """Starts or stops each sysinfo timer to match the window and quiet
        mode: nothing polls while minimized (nobody sees the values, and the
        sensor and VPN checks spawn processes), and the network ones (ping,
        VPN) never run on their own in quiet mode. The winget auto-check
        timer follows the same rules."""
        if self._closed:
            return
        visible = not self._window_minimized
        network = visible and not self.settings.quiet_mode
        for timer, tick, active in (
            (self._sysinfo_timer, self._on_sysinfo_tick, visible),
            (self._hw_sensor_timer, self._on_hw_sensor_tick, visible),
            (self._ping_timer, self._on_ping_tick, network),
            (self._vpn_timer, self._on_vpn_tick, network),
        ):
            if timer is None:
                continue
            if active and not timer.isActive():
                timer.start()
                # Fresh values right away instead of one interval late.
                tick()
            elif not active and timer.isActive():
                timer.stop()
        # The winget auto-check follows the same two rules; its timer lives
        # in the panel, so the panel re-applies it.
        if self._winget_apply_auto_check is not None:
            self._winget_apply_auto_check()

    def _on_export_diagnostics_clicked(self) -> None:
        members = [name for name, _ in diagnostics.collect_members(self.state_dir, self.run_id)]
        listing = "\n".join(f"- {name}" for name in members) or self._t("export_diagnostics_confirm_empty")
        answer = QMessageBox.question(
            self,
            self._t("export_diagnostics_button"),
            self._t("export_diagnostics_confirm").format(members=listing),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        default_name = f"PortableFix-diagnostics-{self.run_id}.zip"
        dest, _ = QFileDialog.getSaveFileName(
            self, self._t("export_diagnostics_button"), default_name, "Zip (*.zip)"
        )
        if not dest:
            return
        try:
            diagnostics.export_diagnostics_zip(self.state_dir, Path(dest), run_id=self.run_id)
        except OSError as exc:
            self._show_error(self, self._t("export_diagnostics_failed"), str(exc))
            return
        # Nothing opens a GitHub issue from here: the user does that with
        # the Report a Bug button, on purpose (the tracker is public).
        opened = QMessageBox.question(
            self,
            self._t("app_title"),
            self._t("export_diagnostics_saved").format(path=dest),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if opened == QMessageBox.Yes:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(dest).parent)))

    def _on_report_bug_clicked(self) -> None:
        QDesktopServices.openUrl(QUrl(diagnostics.build_bug_report_url(APP_VERSION)))

    def _start_sysinfo_polling(self) -> None:
        self._static_info_runner = sysinfo.StaticInfoRunner(parent=self)
        self._static_info_runner.static_info_ready.connect(self._on_static_info_ready)
        self._static_info_runner.start()

        # Only created and given their interval here; _apply_polling_state
        # below starts the ones the current mode allows (quiet mode leaves
        # ping and VPN stopped) and fires each started one once.
        self._sysinfo_timer = QTimer(self)
        self._sysinfo_timer.timeout.connect(self._on_sysinfo_tick)
        self._sysinfo_timer.setInterval(2000)

        self._hw_sensor_timer = QTimer(self)
        self._hw_sensor_timer.timeout.connect(self._on_hw_sensor_tick)
        self._hw_sensor_timer.setInterval(2500)

        self._ping_timer = QTimer(self)
        self._ping_timer.timeout.connect(self._on_ping_tick)
        self._ping_timer.setInterval(4000)

        # VPN state doesn't change on a 4s cadence like ping does, and unlike
        # ping.exe, checking it spawns a full powershell.exe - a much longer
        # interval avoids needless CPU/battery cost from a process spawn
        # every few seconds for the whole session.
        self._vpn_timer = QTimer(self)
        self._vpn_timer.timeout.connect(self._on_vpn_tick)
        self._vpn_timer.setInterval(60_000)
        self._apply_polling_state()

    def _on_static_info_ready(self, info: sysinfo.StaticInfo) -> None:
        if self._closed:
            return
        self._sysinfo_labels["os"].setText(info.os_name)
        self._sysinfo_labels["cpu_name"].setText(f"{info.cpu_name} ({self._tn('sysinfo_cores', info.cpu_cores, 'core')})")
        self._sysinfo_labels["ram_speed"].setText(
            f"{info.ram_speed_mhz} MHz" if info.ram_speed_mhz else self._t("sysinfo_na")
        )
        self._sysinfo_labels["disk_health"].setText(info.disk_health_summary or self._t("sysinfo_na"))
        self._sysinfo_labels["ip"].setText(info.local_ip)

    @staticmethod
    def _format_uptime(seconds: float) -> str:
        total_minutes = int(seconds // 60)
        days, rem_minutes = divmod(total_minutes, 24 * 60)
        hours, minutes = divmod(rem_minutes, 60)
        if days > 0:
            return f"{days}d {hours}h {minutes}m"
        return f"{hours}h {minutes}m"

    def _on_sysinfo_tick(self) -> None:
        if self._closed:
            return
        cpu_load = self._cpu_load_sampler.sample()
        self._sysinfo_labels["cpu_load"].setText(f"{cpu_load:.0f}%" if cpu_load is not None else self._t("sysinfo_na"))
        used_gb, total_gb = sysinfo.get_ram_usage_gb()
        self._sysinfo_labels["ram"].setText(f"{used_gb} / {total_gb} GB")
        self._sysinfo_labels["uptime"].setText(self._format_uptime(sysinfo.get_uptime_seconds()))
        battery = sysinfo.get_battery_percent()
        self._sysinfo_labels["battery"].setText(f"{battery}%" if battery is not None else self._t("sysinfo_na"))

    def _on_hw_sensor_tick(self) -> None:
        if self._closed or self._hw_sensor_busy:
            return
        self._hw_sensor_busy = True
        self._hw_sensor_runner = sysinfo.HardwareSensorRunner(self.assets_dir, parent=self)
        self._hw_sensor_runner.sensors_ready.connect(self._on_hw_sensors_ready)
        self._hw_sensor_runner.start()

    def _on_hw_sensors_ready(self, hw: dict) -> None:
        self._hw_sensor_busy = False
        if self._closed:
            return
        self._sysinfo_labels["cpu_clock"].setText(
            f"{hw['cpu_clock_mhz']:.0f} MHz" if hw["cpu_clock_mhz"] is not None else self._t("sysinfo_na")
        )
        self._sysinfo_labels["gpu_name"].setText(hw["gpu_name"] or self._t("sysinfo_na"))
        self._sysinfo_labels["gpu_load"].setText(
            f"{hw['gpu_load_percent']:.0f}%" if hw["gpu_load_percent"] is not None else self._t("sysinfo_na")
        )
        self._sysinfo_labels["gpu_temp"].setText(
            f"{hw['gpu_temp_c']:.0f}°C" if hw["gpu_temp_c"] is not None else self._t("sysinfo_na")
        )
        self._sysinfo_labels["gpu_clock"].setText(
            f"{hw['gpu_clock_mhz']:.0f} MHz" if hw["gpu_clock_mhz"] is not None else self._t("sysinfo_na")
        )
        if hw["gpu_vram_used_gb"] is not None and hw["gpu_vram_total_gb"] is not None:
            self._sysinfo_labels["gpu_vram"].setText(f"{hw['gpu_vram_used_gb']} / {hw['gpu_vram_total_gb']} GB")
        else:
            self._sysinfo_labels["gpu_vram"].setText(self._t("sysinfo_na"))

    def _on_ping_tick(self) -> None:
        if self._closed or self._ping_busy:
            return
        self._ping_busy = True
        self._ping_runner = sysinfo.PingRunner(parent=self)
        self._ping_runner.ping_ready.connect(self._on_ping_ready)
        self._ping_runner.start()

    def _on_ping_ready(self, latency_ms: float | None) -> None:
        self._ping_busy = False
        if self._closed:
            return
        self._sysinfo_labels["ping"].setText(f"{latency_ms:.0f} ms" if latency_ms is not None else self._t("sysinfo_na"))

    def _on_vpn_tick(self) -> None:
        if self._closed or self._vpn_busy:
            return
        self._vpn_busy = True
        self._vpn_runner = sysinfo.VpnStatusRunner(parent=self)
        self._vpn_runner.vpn_status_ready.connect(self._on_vpn_status_ready)
        self._vpn_runner.start()

    def _on_vpn_status_ready(self, name: str | None) -> None:
        self._vpn_busy = False
        if self._closed:
            return
        if name is None:
            text = self._t("sysinfo_na")
        elif name == "":
            text = self._t("sysinfo_vpn_off")
        else:
            text = self._t("sysinfo_vpn_connected").format(name=name)
        self._sysinfo_labels["vpn"].setText(text)

    def _on_speed_test_clicked(self) -> None:
        if self._speed_test_busy:
            return
        self._speed_test_busy = True
        self.speed_test_button.setEnabled(False)
        self._speed_test_stage_values = {"download": None, "upload": None, "ping": None}
        self._speed_test_stage_done: set[str] = set()
        self._render_speed_test_result()
        self._speed_test_runner = sysinfo.SpeedTestRunner(parent=self)
        self._speed_test_runner.stage_ready.connect(self._on_speed_test_stage_ready)
        self._speed_test_runner.all_finished.connect(self._on_speed_test_all_finished)
        self._speed_test_runner.start()

    def _on_speed_test_stage_ready(self, stage: str, value: object) -> None:
        if self._closed:
            return
        self._speed_test_stage_values[stage] = value
        self._speed_test_stage_done.add(stage)
        self._render_speed_test_result()

    def _render_speed_test_result(self) -> None:
        stage_labels = {
            "download": self._t("sysinfo_speed_download"),
            "upload": self._t("sysinfo_speed_upload"),
            "ping": self._t("sysinfo_ping"),
        }
        lines = []
        for stage in ("download", "upload", "ping"):
            value = self._speed_test_stage_values.get(stage)
            if stage not in self._speed_test_stage_done:
                text = self._t("sysinfo_speed_testing")
            elif value is None:
                text = self._t("sysinfo_na")
            elif stage == "ping":
                text = f"{value:.0f} ms"
            else:
                text = f"{value:.1f} Mbit/s"
            lines.append(f"{stage_labels[stage]}: {text}")
        self.speed_test_result_label.setText("\n".join(lines))

    def _on_speed_test_all_finished(self) -> None:
        self._speed_test_busy = False
        if self._closed:
            return
        self.speed_test_button.setEnabled(True)
